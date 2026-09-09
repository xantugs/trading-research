#!/usr/bin/env python3
"""Check a downloaded tick tree for truncated or missing Parquet files.

A partial download is the normal failure mode over an unreliable link. Parquet
carries a magic footer, so any file cut short fails to open - that is a stronger
check than comparing sizes, and far cheaper than hashing 26 GB.

    python scripts/verify_tick_download.py C:\\data\\wrds_ticks
    python scripts/verify_tick_download.py C:\\data\\wrds_ticks --delete-bad
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="Local directory holding the downloaded tree")
    ap.add_argument("--expected", type=int, default=2518,
                    help="Expected Parquet files (QQQ 2018-2022 default: 2518; use 0 for manifest total)")
    ap.add_argument("--expected-manifests", type=int, default=60,
                    help="Expected monthly manifests (QQQ 2018-2022 default: 60; use 0 to disable)")
    ap.add_argument("--delete-bad", action="store_true",
                    help="Remove unreadable files so the next sync re-fetches them")
    args = ap.parse_args()

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("pip install pyarrow", file=sys.stderr)
        return 1

    if not args.root.is_dir():
        print(f"No such directory: {args.root}", file=sys.stderr)
        return 1

    files = sorted(args.root.rglob("*.parquet"))
    manifests = sorted(args.root.rglob("manifest.json"))

    declared = 0
    declared_paths = {}
    bad_manifests = []
    for path in manifests:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))["files"]
            declared += len(entries)
            for entry in entries:
                expected_path = (path.parent / entry["file"]).resolve()
                if expected_path in declared_paths:
                    raise ValueError(f"duplicate declared file {entry['file']}")
                declared_paths[expected_path] = entry.get("rows")
        except Exception as exc:
            print(f"unreadable manifest: {path} ({exc})")
            bad_manifests.append(path)

    bad, row_mismatches, rows = [], [], 0
    for path in files:
        try:
            count = pq.ParquetFile(path).metadata.num_rows
            rows += count
            expected_rows = declared_paths.get(path.resolve())
            if expected_rows is not None and count != int(expected_rows):
                row_mismatches.append((path, int(expected_rows), count))
        except Exception as exc:
            bad.append((path, exc))

    print(f"manifests   {len(manifests)}")
    print(f"parquet     {len(files)} present, {declared} declared by manifests")
    print(f"rows        {rows:,}")
    print(f"unreadable  {len(bad)}")

    for path, exc in bad[:20]:
        print(f"  BAD {path}: {exc}")
        if args.delete_bad:
            path.unlink(missing_ok=True)
    if len(bad) > 20:
        print(f"  ... and {len(bad) - 20} more")
    if args.delete_bad and bad:
        print("Deleted unreadable files; rerun the sync to fetch them again.")

    for path, expected_rows, actual_rows in row_mismatches[:20]:
        print(f"  ROWS {path}: manifest {expected_rows:,}, parquet {actual_rows:,}")

    present_paths = {path.resolve() for path in files}
    missing_declared = sorted(set(declared_paths) - present_paths)
    unexpected = sorted(present_paths - set(declared_paths)) if declared_paths else []
    if missing_declared:
        print(f"MISSING     {len(missing_declared)} manifest-declared file(s)")
    if unexpected:
        print(f"UNEXPECTED  {len(unexpected)} Parquet file(s) absent from their manifests")

    target = args.expected or declared
    missing = target - len(files) if target else 0
    if missing > 0:
        print(f"MISSING     {missing} file(s) never arrived")
    manifest_gap = max(args.expected_manifests - len(manifests), 0) if args.expected_manifests else 0
    if manifest_gap:
        print(f"MISSING     {manifest_gap} monthly manifest(s) never arrived")
    if declared and args.expected and declared != args.expected:
        print(f"DECLARED    manifests list {declared}, expected {args.expected}")
    return 1 if (bad or bad_manifests or row_mismatches or missing_declared or unexpected
                 or missing > 0 or manifest_gap > 0
                 or (declared and args.expected and declared != args.expected)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
