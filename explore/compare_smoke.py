#!/usr/bin/env python3
"""Compare official Antares CLI vs minimal_agent (Docker) on smoke subset (5 repos)."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "data" / "smoke_manifest.csv"
ZIPS = REPO / "data" / "ghsa-vulns"
PYTHON = REPO / ".venv" / "bin" / "python"
ANTARES = REPO / ".venv" / "bin" / "antares"
MINIMAL = REPO / "minimal_agent.py"
API = "http://127.0.0.1:8080/v1/completions"
MODEL = str(REPO / "models" / "antares-1b-shim")
OUT = Path(__file__).resolve().parent / "runs" / "compare-smoke"


def load_smoke_rows() -> list[dict]:
    rows = []
    with MANIFEST.open() as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def extract_vulnerable(alpha_id: str, dest: Path) -> Path:
    zpath = ZIPS / alpha_id / "pre_push.zip"
    if not zpath.is_file():
        raise FileNotFoundError(zpath)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(dest)
    # Zip layout: single top-level dir or flat files
    children = [p for p in dest.iterdir() if p.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return dest


def cwe_text(row: dict) -> str:
    descriptions = json.loads(row["cwe_description"])
    return "\n\n".join(descriptions)


def cwe_ids_cli(row: dict) -> str:
    return ",".join(json.loads(row["cwes"]))


def ground_truth(row: dict) -> set[str]:
    return set(json.loads(row["ground_truth_files"]))


def hit_any(submitted: list[str], gt: set[str]) -> bool:
    return bool(gt and set(submitted) & gt)


def run_container(repo: Path, cwe: str, trace_path: Path) -> dict:
    cmd = [
        str(PYTHON),
        str(MINIMAL),
        "--repo",
        str(repo),
        "--cwe",
        cwe,
        "--api-base",
        "http://127.0.0.1:8080/v1",
        "--model",
        MODEL,
        "--save-trace",
        str(trace_path),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    wall = time.monotonic() - t0
    if proc.returncode != 0:
        return {"ok": False, "wall_s": wall, "error": proc.stderr[-2000:] or proc.stdout[-2000:]}
    data = json.loads(trace_path.read_text())
    files = data.get("verdict", {}).get("files") or []
    totals = data.get("totals", {})
    return {
        "ok": True,
        "wall_s": round(wall, 2),
        "model_s": totals.get("latency_s"),
        "terminal_calls": _count_terminal_calls(data.get("trace", [])),
        "submitted": files,
        "outcome": data.get("verdict", {}).get("outcome"),
    }


def _count_terminal_calls(trace: list) -> int:
    n = 0
    for msg in trace:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or ""
        if '"name": "terminal"' in content or '"name":"terminal"' in content:
            n += 1
    return n


def run_cli(repo: Path, cwe_ids: str, json_path: Path) -> dict:
    cmd = [
        str(ANTARES),
        "query",
        str(repo),
        "--cwe",
        cwe_ids,
        "--endpoint",
        API,
        "--model",
        MODEL,
        "--format",
        "json",
        "--no-report",
        "--tool-budget",
        "15",
    ]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    wall = time.monotonic() - t0
    if proc.returncode != 0:
        return {"ok": False, "wall_s": round(wall, 2), "error": (proc.stderr or proc.stdout)[-2000:]}
    json_path.write_text(proc.stdout)
    payload = json.loads(proc.stdout)
    summary = payload.get("summary", {})
    findings = [f.get("file_path") for f in payload.get("findings", []) if f.get("file_path")]
    return {
        "ok": True,
        "wall_s": round(wall, 2),
        "tool_calls": summary.get("tool_call_count"),
        "failed_tool_calls": summary.get("failed_tool_calls"),
        "submitted": findings,
        "duration_reported": summary.get("duration_seconds"),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    tmp_root = Path(tempfile.mkdtemp(prefix="antares-compare-"))

    try:
        for row in load_smoke_rows():
            alpha = row["alpha_id"]
            print(f"\n==> {alpha} {row['repo_full_name']}", flush=True)
            repo_dir = extract_vulnerable(alpha, tmp_root / alpha)
            gt = ground_truth(row)
            cwe = cwe_text(row)
            cwe_cli = cwe_ids_cli(row)

            container_trace = OUT / f"{alpha}-container.json"
            cli_json = OUT / f"{alpha}-cli.json"

            print("    container (minimal_agent + Docker)...", flush=True)
            container = run_container(repo_dir, cwe, container_trace)
            print(f"    -> {container.get('wall_s')}s", flush=True)

            print("    CLI (host snapshot)...", flush=True)
            cli = run_cli(repo_dir, cwe_cli, cli_json)
            print(f"    -> {cli.get('wall_s')}s", flush=True)

            c_files = container.get("submitted") or []
            cli_files = cli.get("submitted") or []
            entry = {
                "alpha_id": alpha,
                "repo": row["repo_full_name"],
                "ground_truth": sorted(gt),
                "container": container,
                "cli": cli,
                "container_hit": hit_any(c_files, gt) if container.get("ok") else None,
                "cli_hit": hit_any(cli_files, gt) if cli.get("ok") else None,
            }
            results.append(entry)
            shutil.rmtree(tmp_root / alpha, ignore_errors=True)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    report = {"repos": results}
    for r in results:
        cw, lw = r["container"].get("wall_s"), r["cli"].get("wall_s")
        if cw and lw:
            r["cli_vs_container_wall"] = round(lw - cw, 2)

    total_c = sum(r["container"].get("wall_s", 0) for r in results if r["container"].get("ok"))
    total_l = sum(r["cli"].get("wall_s", 0) for r in results if r["cli"].get("ok"))
    report["summary"] = {
        "n": len(results),
        "container_total_wall_s": round(total_c, 2),
        "cli_total_wall_s": round(total_l, 2),
        "container_hit_any": sum(1 for r in results if r["container_hit"]),
        "cli_hit_any": sum(1 for r in results if r["cli_hit"]),
    }

    out_json = OUT / "comparison.json"
    out_json.write_text(json.dumps(report, indent=2) + "\n")

    lines = [
        "# Smoke subset: Docker (minimal_agent) vs official CLI",
        "",
        f"Endpoint: `{API}` | Model: `{MODEL}`",
        "",
        "## Per repo",
        "",
        "| Repo | GT hit (container) | GT hit (CLI) | Wall container | Wall CLI | Δ (CLI−container) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in results:
        cw = r["container"].get("wall_s", "err")
        lw = r["cli"].get("wall_s", "err")
        delta = r.get("cli_vs_container_wall", "")
        lines.append(
            f"| {r['alpha_id']} | {r['container_hit']} | {r['cli_hit']} | {cw}s | {lw}s | {delta}s |"
        )
    s = report["summary"]
    lines.extend(
        [
            "",
            "## Totals (5 repos)",
            "",
            f"- **Container total wall:** {s['container_total_wall_s']}s",
            f"- **CLI total wall:** {s['cli_total_wall_s']}s",
            f"- **hit@any:** container {s['container_hit_any']}/5, CLI {s['cli_hit_any']}/5",
            "",
            "Raw JSON: `explore/runs/compare-smoke/comparison.json`",
        ]
    )
    (OUT / "comparison.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
