#!/usr/bin/env python3
"""Migrate verified daily MT5 tick CSVs to the compact ORB binary format."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

try:
    from wrds_to_mt5_ticks import (
        MT5_COLUMNS,
        sha256,
        verify_mt5_bin,
        verify_mt5_csv,
        write_mt5_bin,
    )
except ImportError:
    from scripts.wrds_to_mt5_ticks import (
        MT5_COLUMNS,
        sha256,
        verify_mt5_bin,
        verify_mt5_csv,
        write_mt5_bin,
    )


def migrate(path: Path, *, delete_csv: bool, overwrite: bool) -> Path:
    manifest_path = path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_rows = int(manifest["rows"])
    verified_csv = verify_mt5_csv(path, expected_rows=expected_rows)
    if path.stat().st_size != int(manifest["output_size_bytes"]):
        raise ValueError(f"{path}: CSV size differs from manifest")
    if sha256(path) != manifest["output_sha256"]:
        raise ValueError(f"{path}: CSV SHA-256 differs from manifest")
    if verified_csv["first"] != manifest["first"] or verified_csv["last"] != manifest["last"]:
        raise ValueError(f"{path}: CSV timestamp bounds differ from manifest")

    frame = pd.read_csv(path, sep="\t")
    if list(frame.columns) != MT5_COLUMNS:
        raise ValueError(f"{path}: wrong CSV columns")
    times = pd.to_datetime(
        frame["<DATE>"].astype(str) + " " + frame["<TIME>"].astype(str),
        format="%Y.%m.%d %H:%M:%S.%f", errors="raise",
    ).to_numpy(dtype="datetime64[ms]")
    source_day = date.fromisoformat(str(manifest["source_date"]))
    target = path.with_suffix(".bin")
    if target.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {target}; pass --overwrite")
    written = write_mt5_bin(
        target, times,
        frame["<BID>"].to_numpy(dtype=float),
        frame["<ASK>"].to_numpy(dtype=float),
        int(manifest["price_digits"]), source_day,
    )
    verified_bin = verify_mt5_bin(target, expected_rows=expected_rows)
    if {key: verified_bin[key] for key in written} != written:
        raise ValueError(f"{target}: write/verify statistics disagree")

    manifest.update({
        "output_format": "bin",
        "binary_record_layout": (
            "32-byte header; then 12-byte little-endian records: "
            "int32 milliseconds_since_midnight, int32 bid_points, int32 ask_points"
        ),
        "output_file": target.name,
        "output_size_bytes": target.stat().st_size,
        "output_sha256": sha256(target),
        **written,
    })
    staging_manifest = manifest_path.with_name(f".{manifest_path.name}.partial")
    staging_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    staging_manifest.replace(manifest_path)
    if delete_csv:
        path.unlink()
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--pattern", default="*_ticks.csv")
    parser.add_argument("--delete-csv", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    files = sorted(args.directory.glob(args.pattern))
    if not files:
        print(f"No CSVs match {args.pattern} in {args.directory}", file=sys.stderr)
        return 1
    before = sum(path.stat().st_size for path in files)
    outputs = []
    try:
        for index, path in enumerate(files, 1):
            target = migrate(path, delete_csv=args.delete_csv, overwrite=args.overwrite)
            outputs.append(target)
            print(f"[{index}/{len(files)}] {path.name} -> {target.name}", flush=True)
    except (OSError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    after = sum(path.stat().st_size for path in outputs)
    print(f"Ready: {len(outputs)} file(s), {before / 2**20:.1f} MiB -> {after / 2**20:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
