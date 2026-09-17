#!/usr/bin/env python3
"""Archive native MT5 XAUUSD bid/ask ticks by complete UTC calendar day.

This preserves quote order, including distinct records with the same millisecond.
The files are research inputs, not an execution simulator. No orders are sent.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import _inspect_terminal


def _validate_ticks(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> None:
    required = {"time", "time_msc", "bid", "ask", "last", "volume", "flags", "volume_real"}
    if not required.issubset(frame.columns):
        raise ValueError(f"tick fields missing: {sorted(required - set(frame.columns))}")
    if frame.empty:
        return
    clock = frame.time_msc.to_numpy(dtype=np.int64)
    if clock[0] < start.timestamp() * 1000 or clock[-1] >= end.timestamp() * 1000:
        raise ValueError(f"tick outside UTC day {start.date()}")
    if np.any(np.diff(clock) < 0):
        raise ValueError(f"out-of-order tick timestamps on {start.date()}")
    if (frame.time.to_numpy(dtype=np.int64) != clock // 1000).any():
        raise ValueError(f"tick second/millisecond timestamp mismatch on {start.date()}")
    if (not np.isfinite(frame[["bid", "ask"]].to_numpy()).all() or
            (frame.bid <= 0).any() or (frame.ask < frame.bid).any()):
        raise ValueError(f"invalid bid/ask quotes on {start.date()}")


def _progress(done: int, total: int, started: float, rows: int, label: str) -> None:
    elapsed = time.monotonic() - started
    eta = elapsed / done * (total - done) if done else 0.0
    filled = int(30 * done / total)
    print(f"[{'#' * filled}{'-' * (30 - filled)}] {done}/{total} {label} "
          f"{rows:,} ticks | {elapsed / 60:.1f}m ETA {eta / 60:.1f}m", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    parser.add_argument("--start", help="Inclusive UTC date (default: config start)")
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat(),
                        help="Exclusive UTC date (default: today)")
    parser.add_argument("--terminal-path", help="Path to the intended terminal64.exe")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch completed UTC days")
    args = parser.parse_args(argv)
    try:
        import MetaTrader5 as mt5  # type: ignore
        config = load_config(args.config)
        start = pd.Timestamp(args.start or config.tick_start, tz="UTC")
        end = pd.Timestamp(args.end, tz="UTC")
        if end <= start:
            raise ValueError("--end must follow --start")
        days = pd.date_range(start, end, freq="D", inclusive="left", tz="UTC")
        root = Path(config.tick_cache_dir) / config.expected_server / config.symbol
        m1_root = Path(config.cache_dir) / config.expected_server / config.symbol
        root.mkdir(parents=True, exist_ok=True)
        source_path = root / "source.json"
        old_source = (json.loads(source_path.read_text(encoding="utf-8"))
                      if source_path.exists() else None)
        if old_source and (old_source.get("server") != config.expected_server or
                           old_source.get("symbol") != config.symbol):
            raise ValueError("tick cache belongs to another broker or symbol")
        initialized = (mt5.initialize(path=args.terminal_path) if args.terminal_path
                       else mt5.initialize())
        if not initialized:
            raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
        try:
            provenance = _inspect_terminal(mt5, config)
            if old_source and any(old_source.get(key) != provenance.get(key)
                                  for key in ("server", "symbol", "point", "contract_size",
                                              "tick_size", "volume_step", "chart_mode")):
                raise ValueError("cached tick source metadata differs from the connected symbol")
            source_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
            print(f"Connected: {provenance['company']} / {provenance['server']} / "
                  f"{provenance['symbol']}", flush=True)
            print(f"Archiving {len(days)} complete UTC days in {root}", flush=True)
            records = []
            total_ticks = 0
            started = time.monotonic()
            m1_days = set()
            m1_month = None
            for number, day in enumerate(days, 1):
                next_day = day + pd.Timedelta(days=1)
                month = f"{day:%Y%m}"
                if month != m1_month:
                    m1_path = m1_root / f"{month}.parquet"
                    if not m1_path.exists():
                        raise RuntimeError(f"M1 comparison month is missing: {m1_path}; "
                                           "run ftmo-data first")
                    m1_index = pd.read_parquet(m1_path, columns=["open"]).index
                    m1_days = set(m1_index.normalize())
                    m1_month = month
                directory = root / f"{day:%Y}" / f"{day:%m}"
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / f"{day:%Y-%m-%d}.parquet"
                sidecar = target.with_suffix(".json")
                old = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None
                if (not args.refresh and old and
                        old.get("utc_date") == day.date().isoformat() and
                        ((old.get("rows", 0) == 0 and not target.exists()) or
                         (old.get("rows", 0) > 0 and target.exists()))):
                    record = old
                    if "m1_tick_status" not in record:
                        record["m1_tick_status"] = (
                            "matched" if day in m1_days and record["rows"] else
                            "missing_tick_history" if day in m1_days else "market_closed"
                        )
                        sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
                else:
                    if shutil.disk_usage(root).free < 2_000_000_000:
                        raise RuntimeError("under 2 GB free on tick-cache drive; stopping safely")
                    ticks = mt5.copy_ticks_range(config.symbol, day.to_pydatetime(),
                                                 next_day.to_pydatetime(), mt5.COPY_TICKS_ALL)
                    if ticks is None:
                        raise RuntimeError(f"tick request failed on {day.date()}: {mt5.last_error()}")
                    if len(ticks) == 0 and day in m1_days:
                        # An empty successful MT5 response can occur while history syncs.
                        # Retry before recording a genuine broker tick-history gap.
                        for _ in range(2):
                            time.sleep(0.25)
                            ticks = mt5.copy_ticks_range(
                                config.symbol, day.to_pydatetime(),
                                next_day.to_pydatetime(), mt5.COPY_TICKS_ALL)
                            if ticks is None:
                                raise RuntimeError(f"tick retry failed on {day.date()}: "
                                                   f"{mt5.last_error()}")
                            if len(ticks):
                                break
                    frame = pd.DataFrame(ticks)
                    if not frame.empty:
                        frame = frame.loc[(frame.time_msc >= int(day.timestamp() * 1000)) &
                                          (frame.time_msc < int(next_day.timestamp() * 1000))]
                    _validate_ticks(frame, day, next_day)
                    if day not in m1_days and not frame.empty:
                        raise RuntimeError(f"ticks exist but M1 bars are missing on {day.date()}")
                    if frame.empty:
                        if target.exists():
                            raise RuntimeError(f"refresh returned no ticks for existing {target}")
                        record = {"utc_date": day.date().isoformat(), "file": None,
                                  "rows": 0, "first_msc": None, "last_msc": None,
                                  "m1_tick_status": "missing_tick_history" if day in m1_days
                                  else "market_closed"}
                    else:
                        record = {
                            "utc_date": day.date().isoformat(),
                            "file": str(target.relative_to(root)).replace("\\", "/"),
                            "rows": int(len(frame)),
                            "first_msc": int(frame.time_msc.iloc[0]),
                            "last_msc": int(frame.time_msc.iloc[-1]),
                            "m1_tick_status": "matched",
                        }
                        partial = target.with_suffix(".parquet.partial")
                        frame.to_parquet(partial, index=False, compression="zstd")
                        partial.replace(target)
                    sidecar.write_text(json.dumps(record, indent=2), encoding="utf-8")
                records.append(record)
                total_ticks += record["rows"]
                if number == 1 or number == len(days) or number % max(1, len(days) // 100) == 0:
                    _progress(number, len(days), started, total_ticks, record["utc_date"])
        finally:
            mt5.shutdown()
        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": provenance,
            "start_utc": start.isoformat(),
            "end_exclusive_utc": end.isoformat(),
            "holdout_start_utc": pd.Timestamp(config.holdout_start, tz="UTC").isoformat(),
            "days": records,
        }
        path = root / "download_manifest.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Saved {total_ticks:,} broker ticks across {len(days)} calendar days; "
              f"manifest: {path}", flush=True)
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"Tick download stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
