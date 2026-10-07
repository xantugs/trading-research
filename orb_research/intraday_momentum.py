"""Two published intraday momentum strategies on M1 bid bars (docs/INTRADAY_MOMENTUM_PLAN.md).

S1: Gao, Han, Li & Zhou (2018). Trade the last half hour, 15:30-16:00 New York, in the
direction of the return from the previous 16:00 close to 10:00.
S2: Zarattini, Aziz & Barbon (2024), without VWAP. At each half hour from 10:00 to 15:30,
trade breakouts of a "noise area" around the open sized by the average move at that time
of day over the previous 14 days. Everything is flat at 16:00.

The price "at" a time is the close of the minute bar that ends then. Longs buy at the ask
(bid + spread) and sell at the bid; shorts the reverse; every fill also pays slippage.
Returns are fractions of the entry price at 1x notional.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NY = "America/New_York"
OPEN, CLOSE = 570, 960            # 09:30 and 16:00 as minutes of the day
CHECKPOINTS = tuple(range(600, 931, 30))  # 10:00 ... 15:30


def session_grid(m1: pd.DataFrame, spread_price: np.ndarray):
    """Day x minute tables of close and spread for 09:30-16:00, plus each day's 09:30 open."""
    local = m1.index.tz_convert(NY)
    minute = local.hour * 60 + local.minute
    keep = (minute >= OPEN) & (minute < CLOSE)
    day = pd.Index(local.date[keep], name="date")
    frame = pd.DataFrame({"date": day, "minute": minute[keep], "open": m1.open.to_numpy()[keep],
                          "close": m1.close.to_numpy()[keep], "spread": spread_price[keep]})
    close = frame.pivot(index="date", columns="minute", values="close").reindex(columns=range(OPEN, CLOSE))
    spread = frame.pivot(index="date", columns="minute", values="spread").reindex(columns=range(OPEN, CLOSE))
    # A missing minute takes the last price at most five minutes old; older gaps stay empty.
    close, spread = close.ffill(axis=1, limit=5), spread.ffill(axis=1, limit=5)
    first = frame[frame.minute == OPEN].set_index("date").open.reindex(close.index)
    return close, spread, first


def _fill(direction: int, entry_bid: float, exit_bid: float, s_in: float, s_out: float, slip: float) -> float:
    if direction > 0:
        entry, exit_ = entry_bid + s_in + slip * s_in, exit_bid - slip * s_out
        return (exit_ - entry) / entry_bid
    entry, exit_ = entry_bid - slip * s_in, exit_bid + s_out + slip * s_out
    return (entry - exit_) / entry_bid


def s1_last_half_hour(close: pd.DataFrame, spread: pd.DataFrame, slip: float = 0.1) -> pd.DataFrame:
    prev_close = close[CLOSE - 1].shift(1)
    rows = []
    for day in close.index:
        p10, p1530, p16 = close.at[day, 599], close.at[day, 929], close.at[day, CLOSE - 1]
        prev = prev_close.at[day]
        if not np.isfinite([p10, p1530, p16, prev]).all() or p10 == prev:
            continue
        direction = 1 if p10 > prev else -1
        ret = _fill(direction, p1530, p16, spread.at[day, 929], spread.at[day, CLOSE - 1], slip)
        rows.append({"date": day, "direction": direction, "trades": 1, "ret": ret})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(columns=["direction", "trades", "ret"])


def s2_noise_area(close: pd.DataFrame, spread: pd.DataFrame, first_open: pd.Series,
                  slip: float = 0.1, lookback: int = 14) -> pd.DataFrame:
    prices = close[[c - 1 for c in CHECKPOINTS]].to_numpy()
    moves = np.abs(prices / first_open.to_numpy()[:, None] - 1)
    # Average over the previous `lookback` days that have the price; a half-day or a data gap
    # must not blank the next two weeks (at least 10 of the 14 days are required).
    sigma = pd.DataFrame(moves).rolling(lookback, min_periods=min(10, lookback)).mean().shift(1).to_numpy()
    prev_close = close[CLOSE - 1].shift(1).to_numpy()
    rows = []
    for i, day in enumerate(close.index):
        open_, prev = first_open.iat[i], prev_close[i]
        last, s_last = close.iat[i, -1], spread.iat[i, -1]
        if not np.isfinite([open_, prev, last]).all() or not np.isfinite(sigma[i]).all():
            continue
        upper = max(open_, prev) * (1 + sigma[i])
        lower = min(open_, prev) * (1 - sigma[i])
        row_close, row_spread = close.iloc[i].to_numpy(), spread.iloc[i].to_numpy()
        position, entry, s_entry, total, trades, worst, start_col = 0, 0.0, 0.0, 0.0, 0, 0.0, 0

        def marked_low(end_col: int) -> float:
            """Lowest day P/L while the open position ran: realized so far plus its exit value
            at every minute close (what an equity-based daily loss limit sees)."""
            bids, spreads = row_close[start_col:end_col + 1], row_spread[start_col:end_col + 1]
            ok = np.isfinite(bids) & np.isfinite(spreads)
            if not ok.any():
                return total
            values = _fill(position, entry, bids[ok], s_entry, spreads[ok], slip)
            return total + float(np.min(values))

        for k, minute in enumerate(CHECKPOINTS):
            price, s_now = prices[i, k], spread.at[day, minute - 1]
            if not np.isfinite(price):
                continue
            if (position > 0 and price <= upper[k]) or (position < 0 and price >= lower[k]):
                worst = min(worst, marked_low(minute - 1 - OPEN))
                total += _fill(position, entry, price, s_entry, s_now, slip)
                position = 0
            if position == 0 and (price > upper[k] or price < lower[k]):
                position, entry, s_entry, trades = (1 if price > upper[k] else -1), price, s_now, trades + 1
                start_col = minute - 1 - OPEN
        if position:
            worst = min(worst, marked_low(CLOSE - 1 - OPEN))
            total += _fill(position, entry, last, s_entry, s_last, slip)
        rows.append({"date": day, "direction": 0, "trades": trades, "ret": total, "worst": min(worst, total)})
    return (pd.DataFrame(rows).set_index("date") if rows
            else pd.DataFrame(columns=["direction", "trades", "ret", "worst"]))


def summary(daily: pd.DataFrame) -> dict:
    """Mean daily return over the strategy's tradeable days (a day without a breakout counts
    as 0 for S2), its t-statistic and annualised Sharpe."""
    values = daily.ret.to_numpy(float)
    n = len(values)
    sd = values.std(ddof=1) if n > 1 else np.nan
    traded = daily.trades.to_numpy() > 0
    yearly = daily.ret.groupby(pd.to_datetime(pd.Series(daily.index)).dt.year.to_numpy()).mean()
    return {"days": n, "days_traded": int(traded.sum()), "trades": int(daily.trades.sum()),
            "mean_bps": float(values.mean() * 1e4), "t": float(values.mean() / sd * np.sqrt(n)) if sd > 0 else None,
            "hit_rate": float((values[traded] > 0).mean()) if traded.any() else None,
            "sharpe": float(values.mean() / sd * np.sqrt(252)) if sd > 0 else None,
            "yearly_bps": {int(y): float(v * 1e4) for y, v in yearly.items()}}
