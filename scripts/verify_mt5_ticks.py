#!/usr/bin/env python3
"""Verify MT5 tick CSV files produced from WRDS and their manifests."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from wrds_to_mt5_ticks import sha256, verify_mt5_csv
except ImportError:  # imported as scripts.verify_mt5_ticks in tests
    from scripts.wrds_to_mt5_ticks import sha256, verify_mt5_csv


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="One MT5 CSV or a directory of daily CSV files")
    parser.add_argument("--keep-unchanged", action="store_true")
    parser.add_argument("--chunk-rows", type=int, default=250_000)
    args = parser.parse_args(argv)
    files = [args.path] if args.path.is_file() else sorted(args.path.glob("*_ticks.csv"))
    if not files:
        print(f"No *_ticks.csv files found: {args.path}", file=sys.stderr)
        return 1
    problems = []
    total = 0
    for path in files:
        try:
            manifest_path = path.with_suffix(".manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stats = verify_mt5_csv(
                path, expected_rows=int(manifest["rows"]),
                price_changes_only=not args.keep_unchanged,
                chunk_rows=args.chunk_rows,
            )
            if stats["first"] != manifest["first"] or stats["last"] != manifest["last"]:
                raise ValueError("timestamp bounds differ from manifest")
            if path.stat().st_size != int(manifest["output_size_bytes"]):
                raise ValueError("file size differs from manifest")
            if sha256(path) != manifest["output_sha256"]:
                raise ValueError("SHA-256 differs from manifest")
            total += stats["rows"]
            print(f"OK {path.name}: {stats['rows']:,} ticks")
        except Exception as exc:
            problems.append(f"{path}: {exc}")
    print(f"files       {len(files)}")
    print(f"ticks       {total:,}")
    print(f"problems    {len(problems)}")
    for problem in problems[:20]:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
