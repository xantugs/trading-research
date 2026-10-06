#!/usr/bin/env python3
"""Read every cached FTMO M1 month and check source, schema and date coverage."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import _month_bounds, validate_m1_bars
from scripts.download_mt5_m1 import _coverage


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    parser.add_argument("--report", default="outputs/ftmo_m1_quality.json")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        cache = Path(config.cache_dir) / config.expected_server / config.symbol
        manifest = json.loads((cache / "download_manifest.json").read_text(encoding="utf-8"))
        source = json.loads((cache / "source.json").read_text(encoding="utf-8"))
        if (source.get("server") != config.expected_server or
                source.get("symbol") != config.symbol or
                manifest.get("source") != source):
            raise ValueError("source identity differs from configured FTMO data")
        expected_months = list(_month_bounds(manifest["start_utc"][:10],
                                             manifest["end_exclusive_utc"][:10]))
        records = manifest["months"]
        if len(records) != len(expected_months):
            raise ValueError("manifest does not cover every requested UTC month")
        total_rows = total_bytes = zero_spread = 0
        missing_weekdays = []
        high_zero_spread_months = []
        first = last = previous_last = None
        for (left, right), record in zip(expected_months, records, strict=True):
            expected_file = f"{left:%Y%m}.parquet"
            if record["file"] != expected_file:
                raise ValueError(f"out-of-order or missing month at {expected_file}")
            path = cache / expected_file
            sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            if sidecar != record:
                raise ValueError(f"sidecar/manifest mismatch: {path.name}")
            frame = pd.read_parquet(path)
            # A month recorded as an accepted broker gap may hold no bars at all.
            empty_gap = frame.empty and record.get("accepted_gap") is True
            if not empty_gap:
                validate_m1_bars(frame)
            start = pd.Timestamp(record["request_start_utc"])
            end = pd.Timestamp(record["request_end_exclusive_utc"])
            if not left <= start < end <= right:
                raise ValueError(f"bad request bounds: {path.name}")
            if not empty_gap and (frame.index[0] < start or frame.index[-1] >= end):
                raise ValueError(f"out-of-bounds bars: {path.name}")
            if not empty_gap and previous_last is not None and previous_last >= frame.index[0]:
                raise ValueError(f"overlapping months: {path.name}")
            calculated = _coverage(frame, start, end)
            if any(calculated[key] != record[key] for key in calculated):
                raise ValueError(f"coverage/count mismatch: {path.name}")
            expected_partition = ("holdout" if left >= pd.Timestamp(config.holdout_start, tz="UTC")
                                  else "development")
            if record["partition"] != expected_partition:
                raise ValueError(f"holdout label mismatch: {path.name}")
            total_bytes += path.stat().st_size
            missing_weekdays.extend(calculated["missing_weekdays"])
            if empty_gap:
                continue
            first = first or frame.index[0]
            last = previous_last = frame.index[-1]
            total_rows += len(frame)
            zero_spread += calculated["zero_spread_bars"]
            if calculated["zero_spread_bars"] / len(frame) > 0.05:
                high_zero_spread_months.append({
                    "month": f"{left:%Y-%m}",
                    "zero_spread_bars": calculated["zero_spread_bars"],
                    "zero_spread_pct": round(100 * calculated["zero_spread_bars"] / len(frame), 3),
                })
        print(f"Verified {len(records)} Parquet months / {total_rows:,} M1 bars / "
              f"{total_bytes / 1_000_000:.1f} MB")
        print(f"Source: {source['company']} / {source['server']} / {source['symbol']}")
        print(f"UTC bars: {first} through {last}")
        print(f"Weekdays without bars (holidays to review): {len(missing_weekdays)}")
        print(f"Zero-spread bars (fallback required in bar backtests): {zero_spread:,}")
        if high_zero_spread_months:
            print("Months with >5% zero-spread bars: " + ", ".join(
                f"{item['month']} ({item['zero_spread_pct']:.1f}%)"
                for item in high_zero_spread_months))
        report = {
            "source": source, "months": len(records), "rows": total_rows,
            "parquet_bytes": total_bytes, "first_utc": first.isoformat(),
            "last_utc": last.isoformat(), "missing_weekdays": missing_weekdays,
            "zero_spread_bars": zero_spread,
            "high_zero_spread_months": high_zero_spread_months,
            "holdout_start_utc": manifest["holdout_start_utc"],
            "strategy_outcomes_computed": False,
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Quality report: {report_path.resolve()}")
        print("Data-quality checks computed no strategy outcomes. The backtest runner "
              "reads development months only.")
        return 0
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
