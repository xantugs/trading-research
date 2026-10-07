#!/usr/bin/env python3
"""Compare the S2 EA's trade log with the Python engine on the same days and bars.

    python -m scripts.s2_reconcile --log "<Common>\\Files\\S2_<login>_US100.cash.csv"

The EA logs one row per deal (server time). Deals are paired into trades by position id;
each trade is matched to the engine trade of the same New York date, checkpoint and
direction, replayed on FTMO's own M1 bars for the period (downloaded read-only).
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/indices/ftmo_us100_orb_framework_15_15.json"
NY = "America/New_York"


def read_log(path: Path, server_offset_hours: int = 7) -> pd.DataFrame:
    """EA deals paired into trades, with New York times."""
    deals = pd.read_csv(path)
    deals["ny"] = (pd.to_datetime(deals.time) - pd.Timedelta(hours=server_offset_hours)).dt.tz_localize(NY)
    rows = []
    for position, group in deals.groupby("position"):
        opened, closed = group[group.entry == "in"], group[group.entry == "out"]
        if opened.empty or closed.empty:
            continue
        first, last = opened.iloc[0], closed.iloc[-1]
        direction = 1 if first.type == "buy" else -1
        rows.append({"position": position, "date": first.ny.date(), "direction": direction,
                     "entry_time": first.ny, "entry": float(first.price), "exit_time": last.ny,
                     "exit": float(last.price), "ret": direction * (float(last.price) - float(first.price)) / float(first.price),
                     "profit": float(closed.profit.sum() + group.commission.sum() + group.swap.sum())})
    return pd.DataFrame(rows)


def engine_trades(first: date, last: date) -> pd.DataFrame:
    import MetaTrader5 as mt5  # type: ignore

    from orb_research.config import load_config
    from orb_research.data_loader import _from_mt5_array, decode_broker_clock, validate_m1_bars
    from orb_research.intraday_momentum import s2_noise_area, session_grid

    config = load_config(CONFIG)
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        a = datetime.combine(first - timedelta(days=40), datetime.min.time(), timezone.utc)
        b = datetime.combine(last + timedelta(days=2), datetime.min.time(), timezone.utc)
        rates = mt5.copy_rates_range(config.symbol, mt5.TIMEFRAME_M1, a, b)
    finally:
        mt5.shutdown()
    m1 = decode_broker_clock(validate_m1_bars(_from_mt5_array(rates)), config)
    spread = m1.spread.to_numpy() * config.point
    spread = np.where(spread > 0, spread, config.fallback_spread_price)
    close, sp, first_open = session_grid(m1, spread)
    record: list = []
    s2_noise_area(close, sp, first_open, record=record)
    trades = pd.DataFrame(record)
    return trades[(trades.date >= first) & (trades.date <= last)] if len(trades) else trades


def match(live: pd.DataFrame, engine: pd.DataFrame) -> dict:
    """Same New York date, same direction, live entry within 5 minutes after the checkpoint."""
    used, pairs = set(), []
    for _, t in live.iterrows():
        minute = t.entry_time.hour * 60 + t.entry_time.minute
        candidates = engine[(engine.date == t.date) & (engine.direction == t.direction)
                            & (engine.entry_minute <= minute) & (minute < engine.entry_minute + 5)]
        candidates = candidates[~candidates.index.isin(used)]
        if len(candidates):
            used.add(candidates.index[0])
            pairs.append((t.ret, candidates.iloc[0].ret))
    live_r = np.array([p[0] for p in pairs])
    engine_r = np.array([p[1] for p in pairs])
    return {"live_trades": len(live), "engine_trades": len(engine), "matched": len(pairs),
            "share_of_live_matched": len(pairs) / len(live) if len(live) else None,
            "share_of_engine_matched": len(pairs) / len(engine) if len(engine) else None,
            "live_mean_bps": float(live.ret.mean() * 1e4) if len(live) else None,
            "engine_mean_bps": float(engine.ret.mean() * 1e4) if len(engine) else None,
            "matched_return_correlation": float(np.corrcoef(live_r, engine_r)[0, 1]) if len(pairs) > 2 else None,
            "matched_mean_gap_bps": float((live_r - engine_r).mean() * 1e4) if len(pairs) else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args(argv)
    live = read_log(args.log)
    if live.empty:
        raise SystemExit("the log has no closed trades yet")
    engine = engine_trades(min(live.date), max(live.date))
    report = match(live, engine)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
