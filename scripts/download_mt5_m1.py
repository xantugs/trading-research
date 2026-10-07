#!/usr/bin/env python3
"""Cache broker-native XAUUSD M1 bars, including a sealed research holdout.

This command only acquires and checks market data. It does not run a strategy,
inspect returns, or place orders. One Parquet file is written per UTC month so
an interrupted download can resume without repeating completed months.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import (
    _from_mt5_array, _inspect_terminal, _month_bounds, validate_m1_bars,
)


def _coverage(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    expected_days = pd.date_range(start.normalize(), end.normalize(), freq="D", inclusive="left")
    if end != end.normalize():
        expected_days = expected_days.append(pd.DatetimeIndex([end.normalize()]))
    expected_weekdays = expected_days[expected_days.weekday < 5]
    daily_counts = frame.groupby(frame.index.normalize()).size()
    missing_weekdays = [day.date().isoformat() for day in expected_weekdays
                        if day not in daily_counts.index]
    short_days = {day.date().isoformat(): int(count) for day, count in daily_counts.items()
                  if day.weekday() < 5 and count < 1000}
    return {
        "rows": int(len(frame)),
        "first_utc": frame.index[0].isoformat() if len(frame) else None,
        "last_utc": frame.index[-1].isoformat() if len(frame) else None,
        "observed_days": int(len(daily_counts)),
        "expected_weekdays": int(len(expected_weekdays)),
        "missing_weekdays": missing_weekdays,
        "short_weekdays_under_1000_bars": short_days,
        "zero_spread_bars": int((frame.spread == 0).sum()),
    }


def _progress(done: int, total: int, started: float, label: str) -> None:
    elapsed = time.monotonic() - started
    eta = elapsed / done * (total - done) if done else 0.0
    filled = int(30 * done / total)
    print(f"[{'#' * filled}{'-' * (30 - filled)}] {done}/{total} {label} "
          f"elapsed {elapsed / 60:.1f}m ETA {eta / 60:.1f}m", flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat(),
                        help="Exclusive UTC end date (default: today, so current day is omitted)")
    parser.add_argument("--terminal-path", help="Path to the intended terminal64.exe")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch even verified cached months")
    parser.add_argument("--prices-only", action="store_true",
                        help="Allow a non-USD symbol: the cache is for %%-return studies, not money sizing")
    parser.add_argument("--accept-gap", action="append", default=[], metavar="YYYY-MM",
                        help="Accept this month despite missing weekdays (a known broker gap); "
                             "the missing days are recorded in its manifest entry")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        import MetaTrader5 as mt5  # type: ignore
        config = load_config(args.config)
        if config.source != "mt5":
            raise ValueError("data download requires source=mt5")
        start = pd.Timestamp(config.start, tz="UTC")
        end = pd.Timestamp(args.end, tz="UTC")
        if end <= start:
            raise ValueError("--end must follow the configured start")
        months = [(left, right, max(left, start), min(right, end))
                  for left, right in _month_bounds(config.start, args.end)]
        cache = Path(config.cache_dir) / config.expected_server / config.symbol
        cache.mkdir(parents=True, exist_ok=True)
        source_path = cache / "source.json"
        existing_source = (json.loads(source_path.read_text(encoding="utf-8"))
                           if source_path.exists() else None)
        if existing_source and (existing_source.get("server") != config.expected_server or
                                existing_source.get("symbol") != config.symbol):
            raise RuntimeError("cache belongs to a different broker or symbol")
        initialized = (mt5.initialize(path=args.terminal_path) if args.terminal_path
                       else mt5.initialize())
        if not initialized:
            raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
        try:
            provenance = _inspect_terminal(mt5, config, prices_only=args.prices_only)
            if existing_source and any(existing_source.get(key) != provenance.get(key)
                                       for key in ("server", "symbol", "point", "contract_size",
                                                   "tick_size", "volume_step", "chart_mode")):
                raise RuntimeError("cached source metadata differs from the connected symbol")
            source_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
            print(f"Connected: {provenance['company']} / {provenance['server']} / "
                  f"{provenance['symbol']}", flush=True)
            print(f"Caching {len(months)} UTC months in {cache}", flush=True)
            started = time.monotonic()
            records = []
            for number, (left, right, request_start, request_end) in enumerate(months, 1):
                target = cache / f"{left:%Y%m}.parquet"
                sidecar = cache / f"{left:%Y%m}.json"
                cached = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None
                if (not args.refresh and target.exists() and cached and
                        pd.Timestamp(cached["request_start_utc"]) <= request_start and
                        pd.Timestamp(cached["request_end_exclusive_utc"]) >= request_end):
                    records.append(cached)
                    _progress(number, len(months), started, f"{left:%Y-%m} cached")
                    continue
                rates = mt5.copy_rates_range(config.symbol, mt5.TIMEFRAME_M1,
                                             request_start.to_pydatetime(),
                                             request_end.to_pydatetime())
                if rates is None:
                    raise RuntimeError(f"M1 request failed for {left:%Y-%m}: {mt5.last_error()}")
                frame = _from_mt5_array(rates)
                frame = frame.loc[(frame.index >= request_start) & (frame.index < request_end)]
                accepted = f"{left:%Y-%m}" in args.accept_gap
                if not (frame.empty and accepted):  # an accepted gap may be a whole empty month
                    validate_m1_bars(frame)
                coverage = _coverage(frame, request_start, request_end)
                short = (coverage["expected_weekdays"] >= 5 and
                         len(coverage["missing_weekdays"]) / coverage["expected_weekdays"] > 0.2)
                if short and not accepted:
                    raise RuntimeError(f"{left:%Y-%m} has too many missing weekdays: "
                                       f"{coverage['missing_weekdays']} (pass --accept-gap "
                                       f"{left:%Y-%m} if this is a known broker gap)")
                record = {
                    "file": target.name,
                    "request_start_utc": request_start.isoformat(),
                    "request_end_exclusive_utc": request_end.isoformat(),
                    "partition": "holdout" if left >= pd.Timestamp(config.holdout_start, tz="UTC")
                    else "development",
                    **coverage,
                }
                if short:
                    record["accepted_gap"] = True
                    print(f"Accepted broker gap in {left:%Y-%m}: {len(coverage['missing_weekdays'])} "
                          "weekdays without bars", flush=True)
                partial = target.with_suffix(".parquet.partial")
                frame.to_parquet(partial)
                partial.replace(target)
                sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
                records.append(record)
                _progress(number, len(months), started,
                          f"{left:%Y-%m} {len(frame):,} bars")
        finally:
            mt5.shutdown()
        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": provenance,
            "start_utc": start.isoformat(),
            "end_exclusive_utc": end.isoformat(),
            "holdout_start_utc": pd.Timestamp(config.holdout_start, tz="UTC").isoformat(),
            "months": records,
        }
        manifest_path = cache / "download_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Saved {sum(row['rows'] for row in records):,} M1 rows; "
              f"manifest: {manifest_path}", flush=True)
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"Download stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
