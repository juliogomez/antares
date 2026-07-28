#!/usr/bin/env python3
"""Build a serving shim so mlx-lm can serve Antares checkpoints faithfully.

`mlx_lm.server` cannot serve `fdtn-ai/antares-1b` as published. Three things break,
and this script repairs each one *and verifies the repair*, because a benchmark run
against a subtly wrong model is worse than no benchmark run at all.

  1. RoPE schema drift. Antares was serialized by transformers 5.x, which nests RoPE
     settings as `rope_parameters: {"rope_theta": ..., "rope_type": "default"}`.
     mlx-lm 0.31.x reads the transformers 4.x flat `rope_theta` and raises
     `TypeError: ModelArgs.__init__() missing 1 required positional argument`.
     Fix: copy the value to the flat key. Verified: value copied, never invented.

  2. `lm_head.weight` present despite `tie_word_embeddings: true`. mlx-lm then builds
     no output head and rejects the extra tensor with
     `ValueError: Received 1 parameters not in model`.
     Fix: set `tie_word_embeddings: false` so the head is loaded explicitly.
     Verified: we compare `lm_head.weight` against `model.embed_tokens.weight`
     element-wise and report whether they are identical. When identical, the two
     code paths compute the same product and the fix is provably lossless.

  3. mlx-lm eats the tool calls. It infers a tool parser from the chat template and
     strips `<tool_call>` blocks out of `/v1/completions` responses. The official
     `AntaresRunner` parses that markup out of the raw text, so it sees nothing.
     Fix: set `tool_parser_type: null` in tokenizer_config.json.
     Verified: we load the tokenizer and assert `has_tool_calling` is False.

The shim is a directory of symlinks to the real cached weights plus two small patched
JSON files, so it costs kilobytes rather than duplicating multi-GB tensors.

Usage:
    python scripts/build_mlx_shim.py                        # antares-1b -> models/antares-1b-shim
    python scripts/build_mlx_shim.py --repo fdtn-ai/antares-350m
    python scripts/build_mlx_shim.py --skip-load-check      # faster, weaker verification
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "fdtn-ai/antares-1b"


class ShimError(RuntimeError):
    """Raised when an assumption this script depends on no longer holds."""


def fail(msg: str) -> None:
    raise ShimError(msg)


def find_snapshot(repo_id: str) -> Path:
    """Locate the local HF snapshot for repo_id, downloading only if absent."""
    from huggingface_hub import snapshot_download

    try:
        return Path(snapshot_download(repo_id, local_files_only=True))
    except Exception:
        print(f"  not in local cache; downloading {repo_id} ...")
        # HF_HUB_DISABLE_XET is set by the caller (see docs/TROUBLESHOOTING.md):
        # the Xet CDN 403s through TLS-inspecting corporate proxies.
        return Path(snapshot_download(repo_id))


def check_rope(config: dict) -> dict:
    """Flatten transformers 5.x `rope_parameters` into the flat `rope_theta` mlx-lm wants."""
    report = {"fix": "rope_theta", "applied": False}
    rp = config.get("rope_parameters")

    if "rope_theta" in config and rp is None:
        report["note"] = "checkpoint already uses the flat schema; no change needed"
        return report
    if rp is None:
        fail(
            "config.json has neither `rope_theta` nor `rope_parameters`. The RoPE "
            "schema changed again; inspect the checkpoint before serving it."
        )
    if "rope_theta" not in rp:
        fail(f"`rope_parameters` exists but has no `rope_theta` key: {rp!r}")

    rope_type = rp.get("rope_type", "default")
    if rope_type != "default":
        fail(
            f"`rope_parameters.rope_type` is {rope_type!r}, not 'default'. This shim only "
            "knows how to flatten the default (unscaled) case; a scaled RoPE needs an "
            "explicit `rope_scaling` translation that this script does not implement."
        )

    theta = rp["rope_theta"]
    if "rope_theta" in config and config["rope_theta"] != theta:
        fail(
            f"conflicting RoPE values: flat rope_theta={config['rope_theta']} vs "
            f"rope_parameters.rope_theta={theta}. Refusing to guess."
        )

    config["rope_theta"] = theta
    config["rope_scaling"] = None
    report.update(applied=True, rope_theta=theta, rope_type=rope_type)
    return report


def check_lm_head(config: dict, snapshot: Path) -> dict:
    """Decide how to handle a materialised lm_head, verifying tie-ness rather than assuming it."""
    import mlx.core as mx

    report = {"fix": "tie_word_embeddings", "applied": False}
    weight_files = sorted(snapshot.glob("*.safetensors"))
    if not weight_files:
        fail(f"no .safetensors found in {snapshot}")

    keys: set[str] = set()
    holding: dict[str, Path] = {}
    for wf in weight_files:
        for k in mx.load(str(wf)).keys():
            keys.add(k)
            holding[k] = wf

    has_head = "lm_head.weight" in keys
    embed_key = next((k for k in ("model.embed_tokens.weight", "embed_tokens.weight") if k in keys), None)
    report["checkpoint_has_lm_head"] = has_head
    report["config_says_tied"] = bool(config.get("tie_word_embeddings"))

    if not has_head:
        report["note"] = "no lm_head.weight in checkpoint; tied path is correct as published"
        return report
    if embed_key is None:
        fail("checkpoint has lm_head.weight but no embed_tokens weight to compare against")

    head = mx.load(str(holding["lm_head.weight"]))["lm_head.weight"]
    embed = mx.load(str(holding[embed_key]))[embed_key]
    if head.shape != embed.shape:
        fail(f"lm_head {head.shape} and embeddings {embed.shape} differ in shape; cannot reason about tying")

    identical = bool(mx.array_equal(head, embed).item())
    report["lm_head_bit_identical_to_embeddings"] = identical
    report["shape"] = list(head.shape)

    # Either way the correct action is to load the head explicitly, because mlx-lm
    # refuses to load a tensor it did not build a slot for. What changes is the claim
    # we are entitled to make about it.
    config["tie_word_embeddings"] = False
    report["applied"] = True
    if identical:
        report["note"] = (
            "lm_head.weight is bit-identical to the embeddings, so loading it explicitly "
            "computes exactly the same logits as the tied path. Lossless."
        )
    else:
        report["note"] = (
            "WARNING: lm_head.weight DIFFERS from the embeddings, so the published "
            "`tie_word_embeddings: true` is inaccurate. Loading the real head is the "
            "correct behaviour, and the tied path would have been WRONG."
        )
        print("  !! " + report["note"])
    return report


def check_tool_parser(tokenizer_config: dict) -> dict:
    """Disable mlx-lm's tool-call interception so /v1/completions returns raw text."""
    report = {"fix": "tool_parser_type", "applied": False}
    previous = tokenizer_config.get("tool_parser_type", "<absent>")
    tokenizer_config["tool_parser_type"] = None
    report.update(applied=True, previous=previous)
    return report


def build(repo_id: str, out_dir: Path, load_check: bool) -> dict:
    print(f"[1/5] locating snapshot for {repo_id}")
    snapshot = find_snapshot(repo_id)
    print(f"      {snapshot}")

    config = json.loads((snapshot / "config.json").read_text())
    tok_cfg_path = snapshot / "tokenizer_config.json"
    tokenizer_config = json.loads(tok_cfg_path.read_text()) if tok_cfg_path.exists() else {}

    print("[2/5] applying and checking fixes")
    fixes = [
        check_rope(config),
        check_lm_head(config, snapshot),
        check_tool_parser(tokenizer_config),
    ]
    for f in fixes:
        state = "applied" if f["applied"] else "not needed"
        print(f"      - {f['fix']}: {state}")

    print(f"[3/5] writing shim to {out_dir}")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    patched = {"config.json", "tokenizer_config.json"}
    for entry in sorted(snapshot.iterdir()):
        if entry.name in patched:
            continue
        (out_dir / entry.name).symlink_to(entry.resolve())
    (out_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (out_dir / "tokenizer_config.json").write_text(json.dumps(tokenizer_config, indent=2) + "\n")

    print("[4/5] verifying the tokenizer no longer advertises tool calling")
    from mlx_lm.tokenizer_utils import _infer_tool_parser
    from mlx_lm.tokenizer_utils import load as load_tokenizer

    unpatched = load_tokenizer(snapshot)
    inferred = _infer_tool_parser(unpatched.chat_template)
    if inferred is None:
        print(
            "      note: mlx-lm no longer infers a tool parser for this chat template, "
            "so fix 3 has become a no-op. Harmless, but the workaround can be retired."
        )
    else:
        print(f"      unpatched checkpoint would have used tool parser {inferred!r} (fix is load-bearing)")
    fixes[2]["would_have_inferred"] = inferred

    tok = load_tokenizer(out_dir)
    if getattr(tok, "has_tool_calling", False):
        fail(
            "shim tokenizer still reports has_tool_calling=True. mlx-lm would strip "
            "<tool_call> blocks out of /v1/completions and the Antares runner would "
            "see no tool calls. The tool_parser_type override no longer works."
        )
    print("      shim: has_tool_calling=False (raw <tool_call> markup will pass through)")

    arch = {
        "model_type": config.get("model_type"),
        "num_hidden_layers": config.get("num_hidden_layers"),
        "layer_types": _summarise_layers(config.get("layer_types")),
        "num_local_experts": config.get("num_local_experts"),
        "num_attention_heads": config.get("num_attention_heads"),
        "num_key_value_heads": config.get("num_key_value_heads"),
        "max_position_embeddings": config.get("max_position_embeddings"),
    }

    if load_check:
        print("[5/5] load check: generating a few tokens through mlx-lm")
        from mlx_lm import generate, load

        model, tokenizer = load(str(out_dir))
        text = generate(
            model,
            tokenizer,
            prompt="<|start_of_role|>user<|end_of_role|>Say OK.<|end_of_text|>\n"
            "<|start_of_role|>assistant<|end_of_role|>",
            max_tokens=8,
            verbose=False,
        )
        if not text.strip():
            fail("load check produced empty output; the shim serves a broken model")
        print(f"      generated: {text.strip()[:60]!r}")
    else:
        print("[5/5] load check skipped (--skip-load-check)")

    return {"repo_id": repo_id, "snapshot": str(snapshot), "shim": str(out_dir),
            "fixes": fixes, "architecture": arch}


def _summarise_layers(layer_types) -> dict | None:
    if not layer_types:
        return None
    from collections import Counter

    return dict(Counter(layer_types))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"HF repo id (default: {DEFAULT_REPO})")
    ap.add_argument("--out", default=None, help="shim output dir (default: models/<name>-shim)")
    ap.add_argument("--skip-load-check", action="store_true", help="skip the generate() smoke test")
    ap.add_argument("--report", default=None, help="write a JSON report of what was changed")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else REPO_ROOT / "models" / f"{args.repo.split('/')[-1]}-shim"

    try:
        report = build(args.repo, out_dir, load_check=not args.skip_load_check)
    except ShimError as e:
        print(f"\nFAILED: {e}\n", file=sys.stderr)
        print(
            "This script deliberately refuses to guess. A new mlx-lm or a new checkpoint "
            "revision has broken an assumption documented at the top of this file.",
            file=sys.stderr,
        )
        return 1

    print(f"\nshim ready: {out_dir}")
    print(f"architecture: {json.dumps(report['architecture'])}")
    print(f"\nserve it with:\n  python -m mlx_lm server --model {out_dir} --host 127.0.0.1 --port 8080")
    print(f"note: requests must set \"model\": \"{out_dir}\" (mlx_lm.server routes on that field)")

    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        print(f"report written to {args.report}")
    return 0


if __name__ == "__main__":
    if "HF_HUB_DISABLE_XET" not in os.environ:
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    sys.exit(main())
