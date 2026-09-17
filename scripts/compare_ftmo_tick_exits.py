#!/usr/bin/env python3
"""Compare selected M1 exits with chronological native FTMO bid/ask touches."""
from __future__ import annotations

import json
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    args = parser.parse_args()
    config = load_config(args.config)
    trades = pd.read_csv(Path(config.output_dir) / "trades.csv")
    selected = trades[trades.session_date.isin(
        ["2024-03-08", "2024-03-11", "2024-11-01", "2024-11-04"])]
    tick_eligible = trades[trades.session_date >= config.tick_start]
    selected = pd.concat([selected, tick_eligible[tick_eligible.ambiguous_m1 == True]])  # noqa: E712
    if selected.empty and not tick_eligible.empty:
        selected = pd.concat([tick_eligible.head(2), tick_eligible.tail(2)])
    selected = selected.drop_duplicates(subset=["session_date", "direction"])
    rows = []
    for trade in selected.itertuples(index=False):
        entry = pd.Timestamp(trade.timestamp)
        day = entry.strftime("%Y-%m-%d")
        file = (Path(config.tick_cache_dir) / config.expected_server / config.symbol
                / entry.strftime("%Y") / entry.strftime("%m") / f"{day}.parquet")
        if not file.exists():
            rows.append({"date": day, "status": "tick_file_missing"})
            continue
        ticks = pd.read_parquet(file, columns=["time_msc", "bid", "ask"])
        entry_msc = entry.value // 1_000_000
        exit_hour, exit_minute = map(int, config.session_exit.split(":"))
        exit_ny = (entry.tz_convert(config.session_timezone).normalize()
                   + pd.Timedelta(hours=exit_hour, minutes=exit_minute))
        if exit_ny.tz_convert("UTC").date() != entry.date():
            rows.append({"date": day, "status": "cross_utc_day_not_compared"})
            continue
        exit_msc = exit_ny.tz_convert("UTC").value // 1_000_000
        ticks = ticks[(ticks.time_msc >= entry_msc) & (ticks.time_msc < exit_msc)]
        if ticks.empty:
            rows.append({"date": day, "status": "session_ticks_missing"})
            continue
        if trade.direction == "long":
            stop_hit = ticks.bid.to_numpy() <= trade.stop
            target_hit = ticks.bid.to_numpy() >= trade.take_profit
        else:
            stop_hit = ticks.ask.to_numpy() >= trade.stop
            target_hit = ticks.ask.to_numpy() <= trade.take_profit
        hits = np.flatnonzero(stop_hit | target_hit)
        if len(hits):
            first = int(hits[0])
            tick_exit = "stop" if stop_hit[first] else "target"
            touch_utc = pd.Timestamp(int(ticks.iloc[first].time_msc), unit="ms", tz="UTC")
        else:
            tick_exit, touch_utc = "time", pd.Timestamp(exit_msc, unit="ms", tz="UTC")
        m1_exit = str(trade.exit_reason).split("_")[0]
        gaps = np.diff(ticks.time_msc.to_numpy())
        rows.append({
            "date": day, "direction": trade.direction, "m1_exit": m1_exit,
            "tick_first_touch": tick_exit, "tick_touch_utc": str(touch_utc),
            "m1_exit_minute_utc": str(trade.exit_time),
            "agrees": m1_exit == tick_exit,
            "tick_records": len(ticks),
            "max_tick_gap_seconds": round(float(gaps.max() / 1000), 1) if len(gaps) else None,
            "status": "compared",
        })
    out = Path(config.output_dir) / "sample_tick_exit_comparison.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Compared {len(rows)} selected M1 exits with native tick order. {out}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
