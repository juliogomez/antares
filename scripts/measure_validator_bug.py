#!/usr/bin/env python3
"""Measure how much of the agent's tool-call budget the validator's quote handling costs.

Rather than reasoning about how often the bug ought to fire, this replays every command the
model actually emitted through both validators and counts the ones whose verdict changes:

  old  the validator at the pinned upstream ref, which splits on every '|'
  new  the same validator with quote-aware pipeline splitting

A command that the old validator rejects and the new one accepts is a budget item the model
spent and got nothing back for. Commands rejected by both are the model's own fault: it
asked for `cd`, or chained with `&&`. They are reported separately, because conflating the
two would overstate the bug.

Usage:
    python scripts/measure_validator_bug.py runs/antares-full [--json out.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def load_validator(src_root: Path, alias: str):
    """Import sandbox.validate_command from a specific checkout, under a private alias.

    Both checkouts declare the same package name, so they cannot be imported normally at
    the same time. Load the single module file directly instead; it imports only stdlib.
    """
    path = src_root / "vulnerability_localization_benchmark" / "sandbox.py"
    if not path.exists():
        raise SystemExit(f"no sandbox.py at {path}")
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module.validate_command


def emitted_commands(trace: list[dict]) -> list[str]:
    """Every command the model asked to run, in order, including rejected ones."""
    commands = []
    for msg in trace:
        if msg.get("role") != "assistant":
            continue
        for blob in TOOL_CALL.findall(msg.get("content") or ""):
            try:
                call = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if call.get("name") == "terminal":
                cmd = (call.get("arguments") or {}).get("command")
                if isinstance(cmd, str) and cmd.strip():
                    commands.append(cmd)
    return commands


def measure(run_dir: Path, validate_old, validate_new) -> dict:
    per_task = []
    flipped_examples: Counter[str] = Counter()
    reasons_both: Counter[str] = Counter()
    totals = Counter()

    for trace_path in sorted(run_dir.glob("*_phase_*_trace.json")):
        try:
            trace = json.loads(trace_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        commands = emitted_commands(trace)
        if not commands:
            continue

        n_flipped = n_rejected_both = 0
        for cmd in commands:
            ok_old = validate_old(cmd)[0]
            ok_new = validate_new(cmd)[0]
            if ok_old and not ok_new:
                totals["newly_rejected"] += 1  # must stay zero; the fix only relaxes
            elif not ok_old and ok_new:
                n_flipped += 1
                flipped_examples[cmd.strip()] += 1
            elif not ok_old:
                n_rejected_both += 1
                reasons_both[validate_old(cmd)[1]] += 1

        totals["commands"] += len(commands)
        totals["flipped"] += n_flipped
        totals["rejected_both"] += n_rejected_both
        totals["tasks"] += 1
        if n_flipped:
            totals["tasks_affected"] += 1

        per_task.append({
            "task": trace_path.name.replace("_trace.json", ""),
            "commands": len(commands),
            "flipped": n_flipped,
            "rejected_both": n_rejected_both,
        })

    commands = totals["commands"] or 1
    rejected_total = totals["flipped"] + totals["rejected_both"]
    return {
        "run_dir": str(run_dir),
        "n_tasks": totals["tasks"],
        "n_commands": totals["commands"],
        "n_rejected_total": rejected_total,
        "rejection_rate": rejected_total / commands,
        "n_rejected_quote_bug": totals["flipped"],
        "quote_bug_rate": totals["flipped"] / commands,
        "n_rejected_model_fault": totals["rejected_both"],
        "share_of_rejections_from_quote_bug": (
            totals["flipped"] / rejected_total if rejected_total else 0.0
        ),
        "n_tasks_affected": totals["tasks_affected"],
        "n_newly_rejected_by_fix": totals["newly_rejected"],
        "reasons_rejected_by_both": reasons_both.most_common(),
        "top_quote_bug_commands": flipped_examples.most_common(15),
        "per_task": per_task,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--old-src", type=Path, default=ROOT / "vendor/vloc-bench/src")
    ap.add_argument("--new-src", type=Path, default=ROOT / "vendor/vloc-bench-pr/src")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    validate_old = load_validator(args.old_src, "_vloc_sandbox_old")
    validate_new = load_validator(args.new_src, "_vloc_sandbox_new")

    # Guard against measuring two copies of the same code.
    probe = 'rg -n "unsafe|from_raw"'
    if validate_old(probe)[0] == validate_new(probe)[0]:
        raise SystemExit(
            f"old and new validators agree on {probe!r}; check --old-src/--new-src, "
            "one of them is not the version you think it is"
        )

    report = measure(args.run_dir, validate_old, validate_new)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))

    r = report
    print(f"{r['run_dir']}: {r['n_tasks']} tasks, {r['n_commands']} commands emitted")
    print(f"  rejected outright          {r['n_rejected_total']:5d}  ({r['rejection_rate']:.1%} of commands)")
    print(f"    quote-split bug          {r['n_rejected_quote_bug']:5d}  "
          f"({r['quote_bug_rate']:.1%} of commands, "
          f"{r['share_of_rejections_from_quote_bug']:.1%} of rejections)")
    print(f"    genuine allowlist misses {r['n_rejected_model_fault']:5d}")
    print(f"  tasks losing >=1 call      {r['n_tasks_affected']:5d} / {r['n_tasks']}")
    print(f"  newly rejected by the fix  {r['n_newly_rejected_by_fix']:5d}  (must be 0)")
    print("\n  rejected by both, by reason:")
    for reason, n in r["reasons_rejected_by_both"][:8]:
        print(f"    {n:5d}  {reason}")
    print("\n  most frequent commands the bug rejected:")
    for cmd, n in r["top_quote_bug_commands"][:10]:
        print(f"    {n:4d}x  {cmd[:96]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
