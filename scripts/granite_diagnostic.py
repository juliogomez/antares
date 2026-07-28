#!/usr/bin/env python3
"""Decide whether Granite's failure is the model's or our serving stack's.

In the smoke set, Granite ends 10 of 10 tasks with `submitted_nothing=True`, and in 9 of
those it never called a submit tool at all. Its assistant turns arrive empty: no content and
no tool calls. The harness nudges it, gets two more empty turns, and gives up.

An empty turn has two very different explanations, and the scores look identical either way:

  A. the model genuinely produced nothing useful, or
  B. mlx_lm.server parsed a tool call out of the text, failed to convert it into the
     OpenAI `tool_calls` field, and returned an empty message — in which case we are
     measuring our server, not Granite.

The generic `vllm` runner uses /v1/chat/completions and depends entirely on the server for
tool parsing, so it cannot tell A from B. This script asks the same model the same question
three ways and prints what actually comes back:

  1. chat/completions WITH tools     — exactly what the harness does
  2. chat/completions WITHOUT tools  — is the model mute, or only mute when tools are passed?
  3. completions (raw text)          — the unmediated output, with no server-side parsing

If 3 contains a well-formed tool call that 1 dropped, the failure is ours and any Granite
arm run through the chat API is measuring the wrong thing.

Requires the granite server on 127.0.0.1:8081 (scripts/serve.sh granite).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vendor" / "vloc-bench" / "src"))

from vulnerability_localization_benchmark.model_runners.base import (  # noqa: E402
    SYSTEM_PROMPT,
    TOOLS_SCHEMA,
)

USER = (
    "Analyze this codebase for the following vulnerability class:\n\n"
    "CWE-22: Path Traversal — The product uses external input to construct a pathname that "
    "should be within a restricted directory, but does not properly neutralize sequences "
    "such as '..' that can resolve to a location outside of that directory.\n\n"
    "The repository is at /workspace/repo/. Use the terminal tool to explore and determine "
    "if this vulnerability exists. Then either submit the vulnerable file(s) or declare no "
    "vulnerability found."
)

# A mid-conversation state: the model has already run one command and seen the output. This
# is where the smoke traces went quiet, so it is the state worth probing.
TOOL_OUTPUT = (
    ".\n|-- README.md\n|-- server.js\n|-- package.json\n`-- lib\n    |-- static.js\n"
    "    `-- router.js\n\n1 directory, 5 files\n\n[14 tool-calls remaining]"
)


def render_granite_prompt(messages: list[dict], tokenizer) -> str:
    """Granite's own chat template, rendered locally so the server does no parsing."""
    return tokenizer.apply_chat_template(
        messages, tools=[t["function"] for t in TOOLS_SCHEMA],
        tokenize=False, add_generation_prompt=True,
    )


def summarise(label: str, payload: dict) -> dict:
    msg = (payload.get("choices") or [{}])[0].get("message") or {}
    text = (payload.get("choices") or [{}])[0].get("text")
    content = msg.get("content")
    calls = msg.get("tool_calls") or []
    record = {
        "probe": label,
        "content_len": len(content or "") if content is not None else None,
        "content": (content or "")[:1200] if content is not None else None,
        "n_tool_calls": len(calls),
        "tool_calls": calls[:3],
        "raw_text": (text or "")[:1600] if text is not None else None,
        "finish_reason": (payload.get("choices") or [{}])[0].get("finish_reason"),
    }
    print(f"--- {label} ---")
    print(f"    finish_reason : {record['finish_reason']}")
    if content is not None:
        print(f"    content       : {len(content)} chars {content[:400]!r}")
    if text is not None:
        print(f"    raw text      : {len(text)} chars\n{text[:900]}")
    print(f"    tool_calls    : {len(calls)}")
    for call in calls[:3]:
        print(f"        {json.dumps(call.get('function', call))[:260]}")
    print()
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default="http://127.0.0.1:8081/v1")
    ap.add_argument("--model", default="ibm-granite/granite-4.0-1b")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER},
    ]
    mid_conversation = messages + [
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_0", "type": "function",
            "function": {"name": "terminal", "arguments": json.dumps({"command": "tree -L 2"})},
        }]},
        {"role": "tool", "tool_call_id": "call_0", "content": TOOL_OUTPUT},
    ]

    records = []
    with httpx.Client(timeout=180.0) as client:
        for label, msgs in (("chat + tools, first turn", messages),
                            ("chat + tools, mid conversation", mid_conversation)):
            resp = client.post(f"{args.api_base}/chat/completions", json={
                "model": args.model, "messages": msgs, "tools": TOOLS_SCHEMA,
                "temperature": 0.0, "max_tokens": 512,
            })
            resp.raise_for_status()
            records.append(summarise(label, resp.json()))

        resp = client.post(f"{args.api_base}/chat/completions", json={
            "model": args.model, "messages": messages,
            "temperature": 0.0, "max_tokens": 512,
        })
        resp.raise_for_status()
        records.append(summarise("chat, NO tools (is it mute in general?)", resp.json()))

        # Raw completions: render Granite's own template ourselves, so nothing server-side
        # can intercept the output. This is the unmediated view of what the model produced.
        from mlx_lm.tokenizer_utils import load as load_tokenizer
        from huggingface_hub import snapshot_download
        snap = snapshot_download(args.model, allow_patterns=["*.json", "*.txt", "*.model"])
        tokenizer = load_tokenizer(Path(snap))
        for label, msgs in (("raw completions, first turn", messages),
                            ("raw completions, mid conversation", mid_conversation)):
            prompt = render_granite_prompt(msgs, tokenizer)
            resp = client.post(f"{args.api_base}/completions", json={
                "model": args.model, "prompt": prompt,
                "temperature": 0.0, "max_tokens": 512,
            })
            resp.raise_for_status()
            records.append(summarise(label, resp.json()))

    chat_calls = records[0]["n_tool_calls"] + records[1]["n_tool_calls"]
    raw_has_text = any((r.get("raw_text") or "").strip() for r in records[-2:])
    print("=" * 72)
    if chat_calls == 0 and raw_has_text:
        print("VERDICT: the chat API returned no tool calls while raw completions returned\n"
              "text. Inspect the raw text above: if it contains a tool call, the failure is\n"
              "in our serving stack and a Granite arm through the chat API measures nothing.")
    elif chat_calls > 0:
        print("VERDICT: the chat API does return tool calls here, so the empty turns in the\n"
              "smoke run are not a blanket serving failure. Granite's collapse is more\n"
              "likely to be its own: it stops calling tools partway through.")
    else:
        print("VERDICT: no tool calls and no raw text. The model is mute under both paths.")

    if args.out:
        args.out.write_text(json.dumps({"model": args.model, "probes": records}, indent=2) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
