#!/usr/bin/env python3
"""Export a run trace for publication, with machine-specific detail removed.

Traces are the most instructive artifact this project produces, so a few are committed.
They must not carry anything environment-specific or secret, so this script rewrites
absolute paths, home directories, container names and anything token-shaped before the
trace leaves runs/.

Usage:
    python scripts/export_trace.py runs/antares-smoke/Kv0mIk0C_phase_a_trace.json \
        --out docs/traces/antares-slab-phase-a.json
"""

from __future__ import annotations

import argparse
import json

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCRUBBERS: list[tuple[re.Pattern, str]] = [
    (re.compile(re.escape(str(REPO_ROOT))), "<REPO>"),
    (re.compile(re.escape(str(Path.home()))), "<HOME>"),
    (re.compile(r"/Users/[^/\s\"']+"), "<HOME>"),
    (re.compile(r"vulnerability_localization_benchmark_[A-Za-z0-9_.\-]+"), "<container>"),
    # Anything token-shaped. None of these should ever appear in a trace; the point is that
    # if one ever does, it does not reach a commit.
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "<REDACTED-TOKEN>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "<REDACTED-TOKEN>"),
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b"), "<REDACTED-TOKEN>"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"), "<REDACTED-JWT>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     "<REDACTED-PRIVATE-KEY>"),
]


def scrub(value):
    if isinstance(value, str):
        for pattern, replacement in SCRUBBERS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trace", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--with-result", action="store_true", help="embed the per-entry scores alongside the trace")
    args = ap.parse_args()

    payload = {"trace": json.loads(args.trace.read_text())}
    if args.with_result:
        result_path = args.trace.with_name(args.trace.name.replace("_trace.json", ".json"))
        if result_path.exists():
            payload = {"result": json.loads(result_path.read_text()), **payload}

    cleaned = scrub(payload)
    text = json.dumps(cleaned, indent=2) + "\n"

    # Belt and braces: the scrubbers above should already have removed these, so a hit here
    # means a scrubber regressed and the trace must not be written.
    for needle in (str(Path.home()), "/Users/"):
        if needle in text:
            raise SystemExit(f"refusing to write: {needle!r} still present after scrubbing")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(cleaned['trace'])} messages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
