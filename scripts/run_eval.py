#!/usr/bin/env python3
"""Run VLoc Bench against a locally served model, recording enough provenance to trust it.

This is a thin wrapper around the upstream CLI. It exists for three reasons:

  1. The harness resolves `data.manifest` and `data.zips_dir` relative to the working
     directory, and its `--n-limit` takes an arbitrary prefix of the manifest rather
     than a chosen subset. We resolve paths absolutely and point at a filtered manifest.
  2. Every run directory gets the *effective* config and a provenance record written
     into it, so a result can always be traced back to a benchmark commit, an mlx-lm
     version and a model revision. Without that, "file F1 was 0.4" is an anecdote.
  3. `--resume` is always on, so an overnight sweep that dies can be restarted.

Usage:
    python scripts/run_eval.py --model antares --subset smoke
    python scripts/run_eval.py --model antares --subset full --phases ab
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH = REPO_ROOT / "vendor" / "vloc-bench"

MODELS = {
    "antares": {"config": "configs/antares.yaml", "served": REPO_ROOT / "models" / "antares-1b-shim"},
    "granite": {"config": "configs/granite.yaml", "served": "ibm-granite/granite-4.0-1b"},
}


def git_sha(path: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return None


def hf_revision(repo_id: str) -> str | None:
    """Resolve the local snapshot hash so results name an exact revision, not a tag."""
    try:
        from huggingface_hub import snapshot_download

        return Path(snapshot_download(repo_id, local_files_only=True)).name
    except Exception:
        return None


def package_version(name: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def provenance(model_key: str, served: str, cfg: dict) -> dict:
    shim_cfg = REPO_ROOT / "models" / "antares-1b-shim" / "config.json"
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model_key": model_key,
        "served_model": served,
        "benchmark_commit": git_sha(BENCH),
        "poc_commit": git_sha(REPO_ROOT),
        "versions": {p: package_version(p) for p in ("mlx", "mlx-lm", "transformers", "requests")},
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "config": cfg,
    }
    if model_key == "antares":
        record["antares_revision"] = hf_revision("fdtn-ai/antares-1b")
        if shim_cfg.exists():
            c = json.loads(shim_cfg.read_text())
            record["shim_config"] = {
                k: c.get(k) for k in ("rope_theta", "tie_word_embeddings", "num_hidden_layers",
                                      "num_attention_heads", "num_key_value_heads",
                                      "max_position_embeddings", "model_type")
            }
    else:
        record["granite_revision"] = hf_revision("ibm-granite/granite-4.0-1b")
    return record


def ensure_subset(subset: str) -> Path:
    manifest = BENCH / "data" / "manifest.csv"
    if subset == "full":
        return manifest
    ids = REPO_ROOT / "configs" / f"{subset}_ids.txt"
    if not ids.exists():
        raise SystemExit(f"no id list for subset {subset!r}: expected {ids}")
    out = REPO_ROOT / "data" / f"{subset}_manifest.csv"
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "make_subset.py"),
         "--ids", str(ids), "--out", str(out)],
        check=True,
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=sorted(MODELS), required=True)
    ap.add_argument("--subset", default="smoke", help="'full' or the name of a configs/<name>_ids.txt list")
    ap.add_argument("--phases", default="ab", choices=["a", "b", "ab"])
    ap.add_argument("--out", default=None, help="run directory (default: runs/<model>-<subset>)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--permissive", action="store_true", help="disable the sandbox command allowlist")
    ap.add_argument("--dry-run", action="store_true", help="write the effective config and stop")
    args = ap.parse_args()

    if not BENCH.exists():
        raise SystemExit("benchmark missing; run scripts/fetch_benchmark.sh")

    spec = MODELS[args.model]
    cfg = yaml.safe_load((REPO_ROOT / spec["config"]).read_text())

    served = str(spec["served"])
    cfg["model"]["name"] = served
    cfg["data"]["manifest"] = str(ensure_subset(args.subset))
    cfg["data"]["zips_dir"] = str(REPO_ROOT / "data" / "ghsa-vulns")
    cfg["eval"]["phases"] = args.phases
    if args.workers is not None:
        cfg["eval"]["workers"] = args.workers

    run_dir = Path(args.out) if args.out else REPO_ROOT / "runs" / f"{args.model}-{args.subset}"
    run_dir.mkdir(parents=True, exist_ok=True)
    eff_cfg = run_dir / "effective_config.yaml"
    eff_cfg.write_text(yaml.safe_dump(cfg, sort_keys=False))
    (run_dir / "provenance.json").write_text(json.dumps(provenance(args.model, served, cfg), indent=2) + "\n")

    print(f"==> run dir      : {run_dir}")
    print(f"==> model        : {served}")
    print(f"==> endpoint     : {cfg['model']['api_base']} (runner {cfg['model']['runner']})")
    print(f"==> manifest     : {cfg['data']['manifest']}")
    print(f"==> phases       : {args.phases}")

    if args.dry_run:
        print("dry run; not invoking the harness")
        return 0

    if shutil.which("docker") is None:
        raise SystemExit("docker not found on PATH")

    cmd = [
        sys.executable, "-m", "vulnerability_localization_benchmark.cli",
        "--config", str(eff_cfg),
        "--api-base", cfg["model"]["api_base"],
        "--model-name", served,
        "--runner", cfg["model"]["runner"],
        "--output-dir", str(run_dir),
        "--phases", args.phases,
        "--workers", str(cfg["eval"]["workers"]),
        "--resume",
    ]
    if args.permissive:
        cmd.append("--permissive")

    env = dict(os.environ)
    env.setdefault("SSL_CERT_FILE", str(Path.home() / ".config" / "corp-ca" / "ca-bundle.pem"))
    env["PYTHONUNBUFFERED"] = "1"

    print(f"==> {' '.join(cmd)}\n")
    return subprocess.run(cmd, cwd=REPO_ROOT, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
