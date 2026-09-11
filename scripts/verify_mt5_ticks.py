#!/usr/bin/env python3
"""Verify MT5 tick files produced from WRDS and their manifests."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from wrds_to_mt5_ticks import sha256, verify_mt5_bin, verify_mt5_csv
except ImportError:  # imported as scripts.verify_mt5_ticks in tests
    from scripts.wrds_to_mt5_ticks import sha256, verify_mt5_bin, verify_mt5_csv


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="One MT5 tick file or a directory of daily files")
    parser.add_argument("--pattern", default="*_ticks.*",
                        help="Filename glob when path is a directory")
    parser.add_argument("--keep-unchanged", action="store_true")
    parser.add_argument("--chunk-rows", type=int, default=250_000)
    args = parser.parse_args(argv)
    files = ([args.path] if args.path.is_file() else
             sorted(path for path in args.path.glob(args.pattern)
                    if path.suffix.lower() in {".bin", ".csv"}))
    if not files:
        print(f"No MT5 tick files found: {args.path}", file=sys.stderr)
        return 1
    problems = []
    total = 0
    for path in files:
        try:
            manifest_path = path.with_suffix(".manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if path.suffix.lower() == ".bin":
                verifier = verify_mt5_bin
            elif path.suffix.lower() == ".csv":
                verifier = verify_mt5_csv
            else:
                raise ValueError("expected a .bin or .csv tick file")
            stats = verifier(
                path, expected_rows=int(manifest["rows"]),
                price_changes_only=not args.keep_unchanged,
                chunk_rows=args.chunk_rows,
            )
            if stats["first"] != manifest["first"] or stats["last"] != manifest["last"]:
                raise ValueError("timestamp bounds differ from manifest")
            if path.suffix.lower() == ".bin" and stats["source_day"] != manifest["source_date"]:
                raise ValueError("binary source day differs from manifest")
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
