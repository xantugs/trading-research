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


def session_grid(m1: pd.DataFrame, spread_price: np.ndarray, zone: str = NY,
                 open_minute: int = OPEN, close_minute: int = CLOSE):
    """Day x minute tables of close and spread for one session in its exchange's local time
    (09:30-16:00 New York by default), plus each day's opening-minute open."""
    local = m1.index.tz_convert(zone)
    minute = local.hour * 60 + local.minute
    keep = (minute >= open_minute) & (minute < close_minute)
    day = pd.Index(local.date[keep], name="date")
    frame = pd.DataFrame({"date": day, "minute": minute[keep], "open": m1.open.to_numpy()[keep],
                          "close": m1.close.to_numpy()[keep], "spread": spread_price[keep]})
    columns = range(open_minute, close_minute)
    close = frame.pivot(index="date", columns="minute", values="close").reindex(columns=columns)
    spread = frame.pivot(index="date", columns="minute", values="spread").reindex(columns=columns)
    # A missing minute takes the last price at most five minutes old; older gaps stay empty.
    close, spread = close.ffill(axis=1, limit=5), spread.ffill(axis=1, limit=5)
    first = frame[frame.minute == open_minute].set_index("date").open.reindex(close.index)
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
                  slip: float = 0.1, lookback: int = 14, record: list | None = None,
                  stop_pct: float | None = None) -> pd.DataFrame:
    """The session comes from the grid's columns: checkpoints every 30 minutes from the open
    + 30 to the close - 30, and everything flat at the close."""
    session_open, session_close = int(close.columns[0]), int(close.columns[-1]) + 1
    checkpoints = tuple(range(session_open + 30, session_close - 29, 30))
    prices = close[[c - 1 for c in checkpoints]].to_numpy()
    moves = np.abs(prices / first_open.to_numpy()[:, None] - 1)
    # Average over the previous `lookback` days that have the price; a half-day or a data gap
    # must not blank the next two weeks (at least 10 of the 14 days are required).
    sigma = pd.DataFrame(moves).rolling(lookback, min_periods=min(10, lookback)).mean().shift(1).to_numpy()
    prev_close = close[session_close - 1].shift(1).to_numpy()
    rows = []
    for i, day in enumerate(close.index):
        open_, prev = first_open.iat[i], prev_close[i]
        last, s_last = close.iat[i, -1], spread.iat[i, -1]
        if not np.isfinite([open_, prev, last]).all() or not np.isfinite(sigma[i]).all():
            continue
        upper = max(open_, prev) * (1 + sigma[i])
        lower = min(open_, prev) * (1 - sigma[i])
        row_close, row_spread = close.iloc[i].to_numpy(), spread.iloc[i].to_numpy()
        position, entry, s_entry, total, trades, worst, start_col, entry_minute = 0, 0.0, 0.0, 0.0, 0, 0.0, 0, 0

        def marked_low(end_col: int) -> float:
            """Lowest day P/L while the open position ran: realized so far plus its exit value
            at every minute close (what an equity-based daily loss limit sees)."""
            bids, spreads = row_close[start_col:end_col + 1], row_spread[start_col:end_col + 1]
            ok = np.isfinite(bids) & np.isfinite(spreads)
            if not ok.any():
                return total
            values = _fill(position, entry, bids[ok], s_entry, spreads[ok], slip)
            return total + float(np.min(values))

        def stopped(end_col: int):
            """A resting stop `stop_pct` from the entry: the first minute close at or beyond it,
            filled at the stop or that close, whichever is worse."""
            if stop_pct is None or position == 0:
                return None
            level = entry * (1 - stop_pct) if position > 0 else entry * (1 + stop_pct)
            for col in range(start_col + 1, end_col + 1):
                px = row_close[col]
                if np.isfinite(px) and ((position > 0 and px <= level) or (position < 0 and px >= level)):
                    return col, (min(px, level) if position > 0 else max(px, level))
            return None

        def stop_out(hit) -> None:
            nonlocal total, position
            col, fill = hit
            ret = _fill(position, entry, fill, s_entry, row_spread[col], slip)
            total += ret
            if record is not None:
                record.append({"date": day, "direction": position, "entry_minute": entry_minute, "entry": entry,
                               "exit_minute": session_open + col + 1, "exit": fill, "ret": ret, "stopped": True})
            position = 0

        for k, minute in enumerate(checkpoints):
            price, s_now = prices[i, k], spread.at[day, minute - 1]
            hit = stopped(minute - 1 - session_open)
            if hit is not None:
                worst = min(worst, marked_low(hit[0]))
                stop_out(hit)
            if not np.isfinite(price):
                continue
            if (position > 0 and price <= upper[k]) or (position < 0 and price >= lower[k]):
                worst = min(worst, marked_low(minute - 1 - session_open))
                ret = _fill(position, entry, price, s_entry, s_now, slip)
                total += ret
                if record is not None:
                    record.append({"date": day, "direction": position, "entry_minute": entry_minute,
                                   "entry": entry, "exit_minute": minute, "exit": price, "ret": ret})
                position = 0
            if position == 0 and (price > upper[k] or price < lower[k]):
                position, entry, s_entry, trades = (1 if price > upper[k] else -1), price, s_now, trades + 1
                start_col, entry_minute = minute - 1 - session_open, minute
        hit = stopped(session_close - 1 - session_open)
        if hit is not None:
            worst = min(worst, marked_low(hit[0]))
            stop_out(hit)
        if position:
            worst = min(worst, marked_low(session_close - 1 - session_open))
            ret = _fill(position, entry, last, s_entry, s_last, slip)
            total += ret
            if record is not None:
                record.append({"date": day, "direction": position, "entry_minute": entry_minute,
                               "entry": entry, "exit_minute": session_close, "exit": last, "ret": ret})
        rows.append({"date": day, "direction": 0, "trades": trades, "ret": total, "worst": min(worst, total)})
    return (pd.DataFrame(rows).set_index("date") if rows
            else pd.DataFrame(columns=["direction", "trades", "ret", "worst"]))


def gap_fade(close: pd.DataFrame, spread: pd.DataFrame, first_open: pd.Series, slip: float = 0.1,
             lookback: int = 14, exit_minute: int = 600) -> pd.DataFrame:
    """M1 of docs/GAP_FADE_PLAN.md: on a large opening gap, trade against it from the close of the
    opening bar until the previous close is reached (gap filled) or `exit_minute` (10:00)."""
    session_open, session_close = int(close.columns[0]), int(close.columns[-1]) + 1
    prev_close = close[session_close - 1].shift(1).to_numpy()
    gaps = first_open.to_numpy() / prev_close - 1
    typical = pd.Series(np.abs(gaps)).rolling(lookback, min_periods=min(10, lookback)).mean().shift(1).to_numpy()
    last_col = exit_minute - 1 - session_open
    rows = []
    for i, day in enumerate(close.index):
        gap, size, prev = gaps[i], typical[i], prev_close[i]
        if not np.isfinite([gap, size, prev]).all():
            continue
        row_close, row_spread = close.iloc[i].to_numpy(), spread.iloc[i].to_numpy()
        entry, s_in = row_close[0], row_spread[0]
        if abs(gap) < size or gap == 0 or not np.isfinite(entry):
            rows.append({"date": day, "trades": 0, "ret": 0.0, "worst": 0.0})
            continue
        direction = -1 if gap > 0 else 1
        exit_col = None
        for col in range(1, last_col + 1):
            price = row_close[col]
            if np.isfinite(price) and ((direction < 0 and price <= prev) or (direction > 0 and price >= prev)):
                exit_col = col
                break
        if exit_col is None:
            finite = np.flatnonzero(np.isfinite(row_close[1:last_col + 1]))
            exit_col = int(finite[-1]) + 1 if len(finite) else 0
        ret = _fill(direction, entry, row_close[exit_col], s_in, row_spread[exit_col], slip)
        path_close, path_spread = row_close[:exit_col + 1], row_spread[:exit_col + 1]
        ok = np.isfinite(path_close) & np.isfinite(path_spread)
        marked = _fill(direction, entry, path_close[ok], s_in, path_spread[ok], slip) if ok.any() else np.array([ret])
        rows.append({"date": day, "trades": 1, "ret": ret, "worst": float(min(0.0, np.min(marked), ret))})
    return (pd.DataFrame(rows).set_index("date") if rows
            else pd.DataFrame(columns=["trades", "ret", "worst"]))


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
