#!/usr/bin/env python3
"""Score a completed run, and take Phase B apart properly.

Why this exists rather than just reading the harness's aggregate.json:

  1. `aggregate.json` is built only from the entries executed inside the process that
     wrote it. A resumed run therefore produces a *partial* aggregate that silently
     omits everything it skipped. We recompute from the per-entry files, which are
     complete by construction.

  2. Phase B's headline metric is `true_negative = submitted_nothing` (see
     vendor/vloc-bench/.../scoring.py). Nothing in that definition distinguishes a
     model that inspected the patched code and *decided* to withhold from a model that
     rambled, never emitted a valid tool call, and got stopped by the loop. Both are
     credited as true negatives. A model that never submits anything scores a perfect
     Phase B and a zero Phase A. So the aggregate TNR, on its own, cannot tell you
     whether a model has judgement. We separate deliberate abstention from protocol
     failure, and we score the two phases *jointly* per repository, which is the only
     view in which "knows the difference between vulnerable and patched" is visible.

Metrics are pre-registered in docs/METHODOLOGY.md and were fixed before the full sweep
finished, so nothing here was chosen after seeing the results.

Usage:
    python scripts/analyze.py runs/antares-full
    python scripts/analyze.py runs/antares-full runs/granite-full --markdown
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

# Tool-result strings emitted by the agent loop when a submission tool actually fires.
# Present in the trace for both runners, which matters because the generic runner does
# not record structured tool calls in the trace at all (agent.py stores only content).
MARK_DECLARED_NONE = "Recorded: no vulnerability found."
MARK_SUBMITTED = "Submitted "


def classify_outcome(run_dir: Path, alpha_id: str, phase: str, result: dict) -> str:
    """One of: submitted | declared_none | no_submission.

    `declared_none` means the model called submit_no_vulnerability_found. `no_submission`
    means it never called any submission tool and the loop gave up on it. The harness
    scores both as an abstention; they are not the same behaviour.
    """
    if not result.get("submitted_nothing"):
        return "submitted"
    trace_path = run_dir / f"{alpha_id}_phase_{phase}_trace.json"
    if trace_path.exists():
        try:
            trace = json.loads(trace_path.read_text())
        except json.JSONDecodeError:
            trace = []
        for msg in trace:
            content = msg.get("content") or ""
            if isinstance(content, str) and MARK_DECLARED_NONE in content:
                return "declared_none"
    return "no_submission"


REJECTED = re.compile(r"ERROR: Command rejected — (.+)")


def wasted_budget(run_dir: Path) -> dict:
    """How much of the 15-call terminal budget the sandbox validator refused.

    Worth measuring separately from model quality. The harness's validate_command splits a
    command on '|' without respecting quotes, so an ordinary ripgrep alternation such as
    `rg -n "unsafe|from_raw" src` is parsed as a three-stage pipeline and rejected with
    "command not allowed: from_raw". That is a harness defect, not a model failure, but it
    consumes the model's budget and therefore depresses the measured score.
    """
    reasons: Counter = Counter()
    total_calls = rejected = 0
    for trace_path in sorted(run_dir.glob("*_trace.json")):
        try:
            trace = json.loads(trace_path.read_text())
        except json.JSONDecodeError:
            continue
        for msg in trace:
            if msg.get("role") != "tool_response":
                continue
            content = msg.get("content") or ""
            if not isinstance(content, str):
                continue
            total_calls += 1
            m = REJECTED.search(content)
            if m:
                rejected += 1
                reason = m.group(1).strip()
                if reason.startswith("command not allowed:"):
                    # Collapse the long tail of pattern fragments into one bucket, but keep
                    # genuinely disallowed binaries (cd, python, git) visible.
                    word = reason.split(":", 1)[1].strip()
                    reason = (f"command not allowed: {word}" if word in {"cd", "python", "git", "sh", "bash"}
                              else "command not allowed: <token inside a quoted pattern>")
                reasons[reason] += 1
    return {
        "tool_responses": total_calls,
        "rejected": rejected,
        "rejection_rate": round(rejected / total_calls, 4) if total_calls else 0.0,
        "reasons": dict(reasons.most_common(8)),
    }


def load_run(run_dir: Path) -> dict:
    entries: dict[str, dict[str, dict]] = defaultdict(dict)
    for path in sorted(run_dir.glob("*_phase_*.json")):
        if path.name.endswith("_trace.json"):
            continue
        stem = path.stem                      # <alpha_id>_phase_<a|b>
        alpha_id, _, phase = stem.rpartition("_phase_")[0], None, stem[-1]
        try:
            result = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if "error" in result:
            continue
        result["outcome"] = classify_outcome(run_dir, alpha_id, phase, result)
        entries[alpha_id][phase] = result
    return entries


def phase_a_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    f1s = [r["file_f1"] for r in rows]
    outcomes = Counter(r["outcome"] for r in rows)
    exact, hit_any = 0, 0
    for r in rows:
        sub, gt = set(r.get("submitted_files") or []), set(r.get("ground_truth_files") or [])
        if gt and sub == gt:
            exact += 1
        if gt and sub & gt:
            hit_any += 1
    return {
        "n": len(rows),
        "mean_file_f1": round(st.mean(f1s), 4),
        "median_file_f1": round(st.median(f1s), 4),
        "mean_precision": round(st.mean([r["precision"] for r in rows]), 4),
        "mean_recall": round(st.mean([r["recall"] for r in rows]), 4),
        # hit@any is the "did it point anywhere useful" measure. F1 punishes a model for
        # naming three files when the patch touched one, even if one of the three is right.
        "hit_any_rate": round(hit_any / len(rows), 4),
        "exact_set_rate": round(exact / len(rows), 4),
        "abstain_rate": round(sum(1 for r in rows if r.get("abstained")) / len(rows), 4),
        "outcomes": dict(outcomes),
        "mean_terminal_calls": round(st.mean([r.get("n_terminal_calls", 0) for r in rows]), 2),
    }


def phase_b_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    outcomes = Counter(r["outcome"] for r in rows)
    tn = sum(1 for r in rows if r.get("true_negative"))
    deliberate = outcomes.get("declared_none", 0)
    failed = outcomes.get("no_submission", 0)
    fp_files = [r.get("n_false_positive_files", 0) for r in rows if not r.get("true_negative")]
    return {
        "n": n,
        # As the harness defines it. Reported for comparability, not as a claim.
        "true_negative_rate": round(tn / n, 4),
        "false_positive_rate": round(1 - tn / n, 4),
        # The decomposition the harness metric hides.
        "deliberate_abstention_rate": round(deliberate / n, 4),
        "protocol_failure_rate": round(failed / n, 4),
        "credited_tn_that_were_failures": round(failed / tn, 4) if tn else 0.0,
        "outcomes": dict(outcomes),
        "mean_false_positive_files": round(st.mean(fp_files), 2) if fp_files else 0.0,
        "mean_terminal_calls": round(st.mean([r.get("n_terminal_calls", 0) for r in rows]), 2),
    }


def paired_metrics(entries: dict) -> dict:
    """Joint A/B view. This is the metric that cannot be gamed by a degenerate policy."""
    pairs = [(v["a"], v["b"]) for v in entries.values() if "a" in v and "b" in v]
    if not pairs:
        return {"n_pairs": 0}

    table = Counter()
    repeats = same_files = 0
    disc = disc_and_correct = 0
    for a, b in pairs:
        a_sub = a["outcome"] == "submitted"
        b_sub = b["outcome"] == "submitted"
        table[f"A_{'submit' if a_sub else 'abstain'}__B_{'submit' if b_sub else 'abstain'}"] += 1
        if a_sub and b_sub:
            repeats += 1
            if set(a.get("submitted_files") or []) == set(b.get("submitted_files") or []):
                same_files += 1
        if a_sub and not b_sub:
            disc += 1
            if a.get("file_f1", 0) > 0:
                disc_and_correct += 1

    n = len(pairs)
    correct_in_a = [(a, b) for a, b in pairs if a.get("file_f1", 0) > 0]
    return {
        "n_pairs": n,
        "contingency": dict(table),
        # Submitted on the vulnerable tree and withheld on the patched tree. The behaviour
        # the two-phase design is actually trying to reward.
        "discrimination_rate": round(disc / n, 4),
        "discrimination_and_correct_rate": round(disc_and_correct / n, 4),
        # Conditional TNR: among repositories where the model DID localise something real,
        # how often did it then withhold on the patched version? This removes the free
        # credit that a never-submitting model collects.
        "conditional_tnr_given_correct_in_a": (
            round(sum(1 for a, b in correct_in_a if b["outcome"] != "submitted") / len(correct_in_a), 4)
            if correct_in_a else None
        ),
        "n_correct_in_a": len(correct_in_a),
        # Of the pairs where it submitted twice, how often was the Phase B answer literally
        # the same file list? A high number means the patch made no difference to it.
        "repeat_submission_identical_rate": round(same_files / repeats, 4) if repeats else None,
        "n_submitted_both_phases": repeats,
    }


def breakdowns(rows: list[dict], key: str, metric: str = "file_f1") -> dict:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key) or "unknown")].append(r.get(metric, 0.0))
    return {
        k: {"n": len(v), f"mean_{metric}": round(st.mean(v), 4)}
        for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    }


def analyse(run_dir: Path) -> dict:
    entries = load_run(run_dir)
    a_rows = [v["a"] for v in entries.values() if "a" in v]
    b_rows = [v["b"] for v in entries.values() if "b" in v]
    report = {
        "run_dir": run_dir.name,
        "n_entries_with_any_result": len(entries),
        "phase_a": phase_a_metrics(a_rows),
        "phase_b": phase_b_metrics(b_rows),
        "paired": paired_metrics(entries),
        "sandbox_rejections": wasted_budget(run_dir),
        "phase_a_by_ecosystem": breakdowns(a_rows, "ecosystem"),
        "phase_a_by_severity": breakdowns(a_rows, "severity"),
        "phase_a_by_n_ground_truth": breakdowns(
            [{**r, "gt_bucket": _bucket(len(r.get("ground_truth_files") or []))} for r in a_rows],
            "gt_bucket",
        ),
    }
    prov = run_dir / "provenance.json"
    if prov.exists():
        p = json.loads(prov.read_text())
        report["provenance"] = {
            "served_model": p.get("served_model"),
            "benchmark_commit": p.get("benchmark_commit"),
            "revision": p.get("antares_revision") or p.get("granite_revision"),
            "versions": p.get("versions"),
        }
    return report


def _bucket(n: int) -> str:
    if n == 0:
        return "0 (no code files in patch)"
    if n == 1:
        return "1 file"
    if n <= 3:
        return "2-3 files"
    return "4+ files"


def markdown(reports: list[dict]) -> str:
    out = ["# Results", "", "## Phase A — localisation on the vulnerable tree", ""]
    rows = ["| run | n | mean file F1 | hit@any | exact set | abstain rate | mean terminal calls |",
            "|---|---|---|---|---|---|---|"]
    for r in reports:
        a = r["phase_a"]
        rows.append(f"| {r['run_dir']} | {a['n']} | {a.get('mean_file_f1')} | {a.get('hit_any_rate')} "
                    f"| {a.get('exact_set_rate')} | {a.get('abstain_rate')} | {a.get('mean_terminal_calls')} |")
    out += rows + ["", "## Phase B — behaviour on the patched tree", ""]
    rows = ["| run | n | TNR (harness) | deliberate abstention | protocol failure | share of TN credit that was failure |",
            "|---|---|---|---|---|---|"]
    for r in reports:
        b = r["phase_b"]
        rows.append(f"| {r['run_dir']} | {b['n']} | {b.get('true_negative_rate')} "
                    f"| {b.get('deliberate_abstention_rate')} | {b.get('protocol_failure_rate')} "
                    f"| {b.get('credited_tn_that_were_failures')} |")
    out += rows + ["", "## Paired — did it tell the two trees apart?", ""]
    rows = ["| run | pairs | discrimination | discrimination & correct | conditional TNR given correct in A | identical repeat submissions |",
            "|---|---|---|---|---|---|"]
    for r in reports:
        p = r["paired"]
        rows.append(f"| {r['run_dir']} | {p.get('n_pairs')} | {p.get('discrimination_rate')} "
                    f"| {p.get('discrimination_and_correct_rate')} | {p.get('conditional_tnr_given_correct_in_a')} "
                    f"| {p.get('repeat_submission_identical_rate')} |")
    return "\n".join(out + rows) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--markdown", action="store_true", help="emit a comparison table")
    ap.add_argument("--json-out", type=Path, help="write the full report as JSON")
    args = ap.parse_args()

    reports = []
    for d in args.run_dirs:
        if not d.exists():
            print(f"skipping missing {d}")
            continue
        reports.append(analyse(d))

    if args.markdown:
        print(markdown(reports))
    else:
        for r in reports:
            print(json.dumps(r, indent=2))
    if args.json_out:
        args.json_out.write_text(json.dumps(reports, indent=2) + "\n")
        print(f"written to {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
