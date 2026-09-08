"""Synthetic M1 data with a CFD-like session structure.

Used for the demo file shipped with the app and for smoke tests.  It is a
random walk - there is NO edge in it, so a backtest on it should look like
noise around zero.  That is the point: it shows what "no edge" looks like
before real data goes in.

Server-time layout (mirrors a broker on UTC+3 quoting a US index CFD):
    01:00 - 16:29  pre-market, quiet
    16:30 - 17:29  US cash open, volatile, tick-volume spike on the 16:30 bar
    17:30 - 22:59  regular session
    23:00 - 23:59  late session, thin

``dst_shift`` optionally moves the whole US session one hour EARLIER for a block
of days.  That reproduces the classic mistake of a broker whose server clock
does not follow US daylight saving: the EA keeps reading the 16:30 candle, but
the real open is at 15:30, and the volume self-check should flag it.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def make_synthetic_m1(
    days: int = 120,
    start: str = "2025-03-03",
    seed: int = 42,
    price: float = 18_000.0,
    dst_shift: tuple[str, str] | None = ("2025-03-10", "2025-03-28"),
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    d = pd.Timestamp(start).date()
    made = 0
    dst_from = pd.Timestamp(dst_shift[0]).date() if dst_shift else None
    dst_to = pd.Timestamp(dst_shift[1]).date() if dst_shift else None

    while made < days:
        if d.weekday() < 5:
            shift = -60 if (dst_shift and dst_from <= d <= dst_to) else 0
            day_df, price = _one_day(d, price, rng, shift)
            frames.append(day_df)
            made += 1
        d += timedelta(days=1)

    out = pd.concat(frames)
    out.index.name = "time"
    return out


def _one_day(d: date, open_price: float, rng: np.random.Generator, shift_minutes: int):
    minutes = np.arange(60, 24 * 60)                       # 01:00 .. 23:59
    times = pd.to_datetime(str(d)) + pd.to_timedelta(minutes, unit="m")
    n = len(minutes)

    # regime by time of day (shifted for the DST demo block)
    open_min = 16 * 60 + 30 + shift_minutes
    tod = minutes
    sigma = np.full(n, 2.5)
    vol_mean = np.full(n, 40.0)
    spread = np.full(n, 250.0)                              # points (0.01) -> 2.5 index points
    reg_open = (tod >= open_min) & (tod < open_min + 60)
    reg_rth = (tod >= open_min + 60) & (tod < 23 * 60 + shift_minutes)
    reg_late = tod >= 23 * 60 + shift_minutes
    sigma[reg_open], vol_mean[reg_open], spread[reg_open] = 9.0, 400.0, 200.0
    sigma[reg_rth], vol_mean[reg_rth], spread[reg_rth] = 5.0, 150.0, 100.0
    sigma[reg_late], vol_mean[reg_late], spread[reg_late] = 3.0, 60.0, 180.0
    # the first bar of the open carries an extra burst of volume
    vol_mean[tod == open_min] = 1200.0

    drift = 0.02
    steps = rng.normal(drift, 1.0, n) * sigma
    steps[tod == open_min] += rng.normal(0.0, 15.0)          # occasional gap at the open
    closes = open_price + np.cumsum(steps)
    opens = np.concatenate([[open_price], closes[:-1]])
    intrabar = np.abs(rng.normal(0.0, 1.0, n)) * sigma * 0.8
    highs = np.maximum(opens, closes) + intrabar
    lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, 1.0, n)) * sigma * 0.8
    volume = rng.poisson(vol_mean).astype(float)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "tick_volume": volume, "spread": spread},
        index=times,
    ).round({"open": 2, "high": 2, "low": 2, "close": 2})
    # next day opens near tonight's close with a small overnight move
    next_open = float(closes[-1] + rng.normal(0.0, 20.0))
    return df, next_open


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Write a synthetic M1 CSV for demos and tests")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", default="data/SYNTHETIC_NAS100_M1.csv")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    df = make_synthetic_m1(days=args.days, seed=args.seed)
    df.to_csv(args.out, float_format="%.2f")
    print(f"wrote {len(df):,} bars to {args.out} ({df.index[0]} .. {df.index[-1]})")
