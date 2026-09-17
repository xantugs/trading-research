#!/usr/bin/env python3
"""Reopen every archived FTMO tick day and compare it with M1 trading days."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config
from scripts.download_mt5_ticks import _validate_ticks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    parser.add_argument("--report", default="outputs/ftmo_tick_quality.json")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        root = Path(config.tick_cache_dir) / config.expected_server / config.symbol
        m1_root = Path(config.cache_dir) / config.expected_server / config.symbol
        source = json.loads((root / "source.json").read_text(encoding="utf-8"))
        manifest = json.loads((root / "download_manifest.json").read_text(encoding="utf-8"))
        if (source.get("server") != config.expected_server or
                source.get("symbol") != config.symbol or
                manifest.get("source") != source):
            raise ValueError("tick source differs from configured FTMO symbol")
        start = pd.Timestamp(manifest["start_utc"])
        end = pd.Timestamp(manifest["end_exclusive_utc"])
        days = pd.date_range(start, end, freq="D", inclusive="left", tz="UTC")
        records = manifest["days"]
        if len(days) != len(records):
            raise ValueError("tick manifest does not include every requested UTC day")
        m1_days = set()
        m1_month = None
        m1_index = None
        rows = bytes_on_disk = empty_days = duplicate_milliseconds = 0
        missing_tick_days = []
        missing_minutes_total = session_missing_total = tick_only_minutes_total = 0
        days_with_missing_minutes = []
        session_days_with_missing_minutes = []
        session_start_hour, session_start_minute = map(int, config.session_open.split(":"))
        session_end_hour, session_end_minute = map(int, config.session_exit.split(":"))
        session_start_minute_of_day = session_start_hour * 60 + session_start_minute
        session_end_minute_of_day = session_end_hour * 60 + session_end_minute
        for day, record in zip(days, records, strict=True):
            month = f"{day:%Y%m}"
            if month != m1_month:
                m1_index = pd.read_parquet(m1_root / f"{month}.parquet", columns=["open"]).index
                m1_days = set(m1_index.normalize())
                m1_month = month
            day_index = m1_index[(m1_index >= day) & (m1_index < day + pd.Timedelta(days=1))]
            m1_minute_ids = day_index.asi8 // 60_000_000_000
            if record["utc_date"] != day.date().isoformat():
                raise ValueError(f"out-of-order tick day at {day.date()}")
            expected = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.parquet"
            sidecar = json.loads(expected.with_suffix(".json").read_text(encoding="utf-8"))
            if sidecar != record:
                raise ValueError(f"tick sidecar/manifest mismatch on {day.date()}")
            if record["rows"] == 0:
                if record["file"] is not None or expected.exists():
                    raise ValueError(f"empty tick day has a data file on {day.date()}")
                if day in m1_days:
                    if record.get("m1_tick_status") != "missing_tick_history":
                        raise ValueError(f"unlabelled tick gap on {day.date()}")
                    missing_tick_days.append(day.date().isoformat())
                else:
                    if record.get("m1_tick_status") != "market_closed":
                        raise ValueError(f"closed-day label mismatch on {day.date()}")
                    empty_days += 1
                tick_minute_ids = np.array([], dtype=np.int64)
                missing_minute_ids = np.setdiff1d(m1_minute_ids, tick_minute_ids,
                                                   assume_unique=True)
                if len(missing_minute_ids):
                    days_with_missing_minutes.append((day.date().isoformat(),
                                                      len(missing_minute_ids)))
                    missing_minutes_total += len(missing_minute_ids)
                    local = pd.to_datetime(missing_minute_ids, unit="m", utc=True).tz_convert(
                        config.session_timezone)
                    local_minute = local.hour * 60 + local.minute
                    session_count = int(((local_minute >= session_start_minute_of_day) &
                                         (local_minute < session_end_minute_of_day)).sum())
                    session_missing_total += session_count
                    if session_count:
                        session_days_with_missing_minutes.append((day.date().isoformat(),
                                                                  session_count))
                continue
            if (record["file"] != str(expected.relative_to(root)).replace("\\", "/") or
                    day not in m1_days or record.get("m1_tick_status") != "matched"):
                raise ValueError(f"tick file identity/trading-day mismatch on {day.date()}")
            frame = pd.read_parquet(expected)
            _validate_ticks(frame, day, day + pd.Timedelta(days=1))
            if (len(frame) != record["rows"] or
                    int(frame.time_msc.iloc[0]) != record["first_msc"] or
                    int(frame.time_msc.iloc[-1]) != record["last_msc"]):
                raise ValueError(f"tick count or time bounds changed on {day.date()}")
            rows += len(frame)
            bytes_on_disk += expected.stat().st_size
            duplicate_milliseconds += int((np.diff(frame.time_msc.to_numpy()) == 0).sum())
            tick_minute_ids = np.unique(frame.time_msc.to_numpy(dtype=np.int64) // 60_000)
            missing_minute_ids = np.setdiff1d(m1_minute_ids, tick_minute_ids,
                                               assume_unique=True)
            tick_only_minutes_total += len(np.setdiff1d(tick_minute_ids, m1_minute_ids,
                                                         assume_unique=True))
            if len(missing_minute_ids):
                days_with_missing_minutes.append((day.date().isoformat(), len(missing_minute_ids)))
                missing_minutes_total += len(missing_minute_ids)
                local = pd.to_datetime(missing_minute_ids, unit="m", utc=True).tz_convert(
                    config.session_timezone)
                local_minute = local.hour * 60 + local.minute
                session_count = int(((local_minute >= session_start_minute_of_day) &
                                     (local_minute < session_end_minute_of_day)).sum())
                session_missing_total += session_count
                if session_count:
                    session_days_with_missing_minutes.append((day.date().isoformat(),
                                                              session_count))
        print(f"Verified {len(days)} UTC days / {rows:,} native FTMO ticks / "
              f"{bytes_on_disk / 1_000_000_000:.2f} GB")
        print(f"Market-closed calendar days: {empty_days:,}; "
              f"same-millisecond records preserved: {duplicate_milliseconds:,}")
        print(f"M1 trading days with no broker ticks: {len(missing_tick_days)}")
        if missing_tick_days:
            print("Tick-history gaps: " + ", ".join(missing_tick_days))
        print(f"M1 minutes with no matching tick minute: {missing_minutes_total:,} "
              f"across {len(days_with_missing_minutes):,} days")
        print(f"Of those, minutes inside the configured session: {session_missing_total:,}")
        print(f"Tick minutes with no matching M1 bar: {tick_only_minutes_total:,}")
        if days_with_missing_minutes:
            worst = sorted(days_with_missing_minutes, key=lambda item: item[1], reverse=True)[:5]
            print("Largest intraday gaps: " + ", ".join(f"{day} ({count})" for day, count in worst))
        if session_days_with_missing_minutes:
            worst_session = sorted(session_days_with_missing_minutes,
                                   key=lambda item: item[1], reverse=True)[:5]
            print("Largest session gaps: " + ", ".join(
                f"{day} ({count})" for day, count in worst_session))
        report = {
            "source": source, "calendar_days": len(days), "tick_rows": rows,
            "parquet_bytes": bytes_on_disk, "market_closed_days": empty_days,
            "same_millisecond_records": duplicate_milliseconds,
            "m1_days_without_ticks": missing_tick_days,
            "m1_minutes_without_ticks": missing_minutes_total,
            "session_minutes_without_ticks": session_missing_total,
            "tick_minutes_without_m1": tick_only_minutes_total,
            "days_with_missing_minutes": [
                {"utc_date": day, "missing_minutes": count}
                for day, count in days_with_missing_minutes
            ],
            "session_days_with_missing_minutes": [
                {"utc_date": day, "missing_minutes": count}
                for day, count in session_days_with_missing_minutes
            ],
            "holdout_start_utc": manifest["holdout_start_utc"],
            "strategy_outcomes_computed": False,
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Quality report: {report_path.resolve()}")
        print("Both bid and ask are present. No trade outcomes were computed, "
              "including on the 2025-onward holdout.")
        return 0
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        print(f"Tick verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
