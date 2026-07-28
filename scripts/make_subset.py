#!/usr/bin/env python3
"""Build a manifest subset from a list of alpha_ids.

The harness's `--n-limit N` takes the first N rows of the manifest, which is an
arbitrary slice rather than a chosen sample. For the smoke set we want specific,
documented entries, so we filter the upstream manifest into a smaller one and point
the config's `data.manifest` at it. The upstream file is never modified.

Usage:
    python scripts/make_subset.py --ids configs/smoke_ids.txt --out data/smoke_manifest.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = REPO_ROOT / "vendor" / "vloc-bench" / "data" / "manifest.csv"


def read_ids(path: Path) -> list[str]:
    ids = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            ids.append(line)
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--manifest", type=Path, default=UPSTREAM)
    args = ap.parse_args()

    if not args.manifest.exists():
        print(f"upstream manifest missing: {args.manifest}\nrun scripts/fetch_benchmark.sh first", file=sys.stderr)
        return 1

    wanted = read_ids(args.ids)
    with args.manifest.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        index = {row["alpha_id"]: row for row in reader}

    missing = [i for i in wanted if i not in index]
    if missing:
        print(f"alpha_ids not found in manifest: {missing}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i in wanted:
            writer.writerow(index[i])

    print(f"wrote {len(wanted)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
