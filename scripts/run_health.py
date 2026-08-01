#!/usr/bin/env python3
"""Refuse to score a sweep that was actually an infrastructure failure.

This exists because we were burned twice by the same shape of accident, and both times the
results looked superficially fine.

The harness treats an API exception as the end of the episode: it stops the loop, records
whatever the agent submitted so far (nothing), and writes a result file. That file is
indistinguishable at a glance from an honest run in which the model looked around and
declined to name a file. In Phase B, where declining is the correct answer, a dead model
server therefore scores as a *perfect* true negative. A sweep against a server that is not
running will report 100% specificity.

Two real incidents:

  1. Two MLX servers on a 24 GB machine exhausted memory. Requests timed out at 180 s.
     74 results recorded, all with zero tool calls.
  2. A stop signal killed a sweep wrapper but orphaned its child, which outlived its model
     server and wrote 960 results in four minutes, every one a connection refusal.

So we fingerprint the timing instead of trusting the count. A model that genuinely decides
without looking still has to generate tokens to say so, which takes seconds. A refused
connection takes milliseconds, and a hung server takes exactly the timeout. Neither looks
like generation.

Used by run_eval.py after every sweep and by analyze.py before it reports anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# A refused connection returns in single-digit milliseconds. Real generation of even the
# shortest possible submission is seconds. Nothing legitimate lands below this.
INSTANT_FAILURE_S = 1.0

# Requests that die on the timeout land within a hair of it, so compare with a margin.
TIMEOUT_FRACTION = 0.95


def classify_health(
    result: dict, timeout_s: float = 180.0, max_terminal_calls: int = 15
) -> tuple[str, str]:
    """Return ("ok" | "infrastructure_failure" | "truncated" | "suspect", reason).

    "truncated" is the partial version of the same disease. An episode that ends without
    calling any submit tool, while it still had budget left, did not decide anything: the
    generate call raised and the loop broke. The harness then records submitted_nothing,
    which Phase B scores as a true negative. Unlike the wholesale failures this is not
    grounds for throwing the run away (the usual cause here is one long generation
    exceeding the client's read timeout, not a dead server), but it must not be counted as
    an abstention either, so it gets its own bucket rather than "ok".
    """
    calls = result.get("n_tool_calls", 0)
    terminal = result.get("n_terminal_calls", 0)
    elapsed = result.get("agent_time_s")

    if calls > 0:
        never_submitted = calls == terminal
        had_budget_left = terminal < max_terminal_calls
        if never_submitted and result.get("submitted_nothing") and had_budget_left:
            return (
                "truncated",
                f"ended after {terminal}/{max_terminal_calls} terminal calls without "
                "calling any submit tool, with budget remaining; the episode was cut "
                "short (usually a read timeout), so its submitted_nothing is not a decision",
            )
        return "ok", "made at least one tool call"
    if elapsed is None:
        return "suspect", "zero tool calls and no agent_time_s to judge by"
    if elapsed < INSTANT_FAILURE_S:
        return (
            "infrastructure_failure",
            f"zero tool calls in {elapsed:.3f}s, too fast to be generation; "
            "the model server almost certainly refused the connection",
        )
    if elapsed >= TIMEOUT_FRACTION * timeout_s:
        return (
            "infrastructure_failure",
            f"zero tool calls and agent_time_s {elapsed:.1f}s pinned at the {timeout_s:.0f}s "
            "timeout; the model server hung rather than answered",
        )
    return (
        "suspect",
        f"zero tool calls in {elapsed:.1f}s, possible but unusual; "
        "the model submitted without looking at anything",
    )


def audit(run_dir: Path, timeout_s: float = 180.0) -> dict:
    files = [p for p in sorted(run_dir.glob("*_phase_*.json")) if "_trace" not in p.name]
    buckets: dict[str, list[dict]] = {
        "ok": [], "infrastructure_failure": [], "truncated": [], "suspect": [],
    }
    for path in files:
        try:
            result = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            buckets["suspect"].append({"file": path.name, "reason": f"unreadable: {exc}"})
            continue
        status, reason = classify_health(result, timeout_s)
        buckets[status].append({"file": path.name, "reason": reason})
    return {
        "run_dir": str(run_dir),
        "n_results": len(files),
        "n_ok": len(buckets["ok"]),
        "n_infrastructure_failure": len(buckets["infrastructure_failure"]),
        "n_truncated": len(buckets["truncated"]),
        "n_suspect": len(buckets["suspect"]),
        "buckets": buckets,
    }


def assert_healthy(run_dir: Path, timeout_s: float = 180.0) -> dict:
    """Raise if a run contains wholesale infrastructure failures.

    Truncated episodes deliberately do not raise: they are individually recoverable and are
    excluded from the abstention numbers by analyze.py rather than invalidating the sweep.
    """
    report = audit(run_dir, timeout_s)
    if report["n_infrastructure_failure"]:
        examples = report["buckets"]["infrastructure_failure"][:3]
        detail = "\n".join(f"    {e['file']}: {e['reason']}" for e in examples)
        raise SystemExit(
            f"REFUSING TO SCORE {run_dir}\n"
            f"  {report['n_infrastructure_failure']} of {report['n_results']} results are "
            f"infrastructure failures, not model behaviour:\n{detail}\n"
            "  These inflate Phase B specificity, because a dead server abstains perfectly.\n"
            "  Delete them, fix the serving stack, and re-run with --resume."
        )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--timeout-s", type=float, default=180.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = audit(args.run_dir, args.timeout_s)
    if args.json:
        print(json.dumps(report, indent=2))
        return 1 if report["n_infrastructure_failure"] else 0

    print(f"{report['run_dir']}: {report['n_results']} results")
    print(f"  ok                     {report['n_ok']}")
    print(f"  truncated episodes     {report['n_truncated']}")
    print(f"  infrastructure failure {report['n_infrastructure_failure']}")
    print(f"  suspect                {report['n_suspect']}")
    for status in ("infrastructure_failure", "truncated", "suspect"):
        for entry in report["buckets"][status][:5]:
            print(f"    [{status}] {entry['file']}: {entry['reason']}")
    return 1 if report["n_infrastructure_failure"] else 0


if __name__ == "__main__":
    sys.exit(main())
