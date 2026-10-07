"""0DTE-era candidates of docs/REGIME_STRATEGIES_PLAN.md on New York session grids.

All three take the tables from `intraday_momentum.session_grid` (09:30-16:00 by default):
`close` and `spread` are day x minute, `first_open` is each day's 09:30 open. The price
"at" a time is the close of the minute bar ending then. Returns are fractions of the
entry price at 1x notional, net of spread, slippage and (R4) swap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .intraday_momentum import _fill

CHECKS = tuple(range(600, 931, 30))   # 10:00 ... 15:30


def _rolling_mean_abs(values: np.ndarray, lookback: int = 14) -> np.ndarray:
    return pd.DataFrame(np.abs(values)).rolling(lookback, min_periods=min(10, lookback)).mean().shift(1).to_numpy()


def _marked_low(direction, entry, closes, s_in, spreads, slip) -> float:
    ok = np.isfinite(closes) & np.isfinite(spreads)
    return float(np.min(_fill(direction, entry, closes[ok], s_in, spreads[ok], slip))) if ok.any() else 0.0


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["trades", "ret", "worst"])


def r2_calm_fade(close: pd.DataFrame, spread: pd.DataFrame, first_open: pd.Series, slip: float = 0.1) -> pd.DataFrame:
    session_open = int(close.columns[0])
    grid = close.to_numpy()
    opens = first_open.to_numpy()
    day_range = (np.nanmax(grid, axis=1) - np.nanmin(grid, axis=1)) / opens
    ranges = pd.Series(day_range)
    calm = (ranges.shift(1) <= ranges.rolling(20, min_periods=15).median().shift(1)).to_numpy()
    check_cols = [c - 1 - session_open for c in CHECKS]
    deviation = grid[:, check_cols] / opens[:, None] - 1
    sigma = _rolling_mean_abs(deviation)
    rows = []
    for i, day in enumerate(close.index):
        if not np.isfinite(opens[i]) or not np.isfinite(sigma[i]).all() or not np.isfinite(grid[i, -1]):
            continue
        row, sp = grid[i], spread.iloc[i].to_numpy()
        result = {"date": day, "trades": 0, "ret": 0.0, "worst": 0.0}
        if calm[i]:
            for k, col in enumerate(check_cols):
                dev = deviation[i, k]
                if not np.isfinite(dev) or abs(dev) <= sigma[i, k]:
                    continue
                direction, entry, s_in = (-1 if dev > 0 else 1), row[col], sp[col]
                # Amendment 1: a protective stop needs room past the entry, so it is the
                # larger of 2 sigma and the entry deviation + 1 sigma.
                stop_dev = max(2 * sigma[i, k], abs(dev) + sigma[i, k])
                exit_col = row.size - 1
                for j in range(col + 1, row.size):
                    if not np.isfinite(row[j]):
                        continue
                    move = row[j] / opens[i] - 1
                    back = (direction < 0 and row[j] <= opens[i]) or (direction > 0 and row[j] >= opens[i])
                    stopped = (direction < 0 and move >= stop_dev) or (direction > 0 and move <= -stop_dev)
                    if back or stopped:
                        exit_col = j
                        break
                ret = _fill(direction, entry, row[exit_col], s_in, sp[exit_col], slip)
                low = _marked_low(direction, entry, row[col:exit_col + 1], s_in, sp[col:exit_col + 1], slip)
                result = {"date": day, "trades": 1, "ret": ret, "worst": min(0.0, low, ret)}
                break
        rows.append(result)
    return pd.DataFrame(rows).set_index("date") if rows else _empty()


def r3_late_reversal(close: pd.DataFrame, spread: pd.DataFrame, first_open: pd.Series, slip: float = 0.1) -> pd.DataFrame:
    session_open = int(close.columns[0])
    grid = close.to_numpy()
    opens = first_open.to_numpy()
    col = 900 - 1 - session_open                     # price at 15:00
    move = grid[:, col] / opens - 1
    typical = _rolling_mean_abs(move.reshape(-1, 1)).ravel()
    rows = []
    for i, day in enumerate(close.index):
        last = grid[i, -1]
        if not np.isfinite([opens[i], move[i], typical[i], last]).all():
            continue
        sp = spread.iloc[i].to_numpy()
        if abs(move[i]) <= typical[i]:
            rows.append({"date": day, "trades": 0, "ret": 0.0, "worst": 0.0})
            continue
        direction = -1 if move[i] > 0 else 1
        ret = _fill(direction, grid[i, col], last, sp[col], sp[-1], slip)
        low = _marked_low(direction, grid[i, col], grid[i, col:], sp[col], sp[col:], slip)
        rows.append({"date": day, "trades": 1, "ret": ret, "worst": min(0.0, low, ret)})
    return pd.DataFrame(rows).set_index("date") if rows else _empty()


def r4_overnight_long(close: pd.DataFrame, spread: pd.DataFrame, first_open: pd.Series,
                      swap_per_night: float, slip: float = 0.1) -> pd.DataFrame:
    """Buy at 16:00 (ask), sell at the next session's 09:30 open (bid); the swap is charged
    per night, three times when the position is held over a Friday."""
    last_close, opens = close.iloc[:, -1].to_numpy(), first_open.to_numpy()
    sp_close, sp_open = spread.iloc[:, -1].to_numpy(), spread.iloc[:, 0].to_numpy()
    dates = pd.to_datetime(pd.Series(close.index))
    rows = []
    for i in range(len(close.index) - 1):
        entry, exit_ = last_close[i], opens[i + 1]
        if not np.isfinite([entry, exit_, sp_close[i]]).all():
            continue
        s_out = sp_open[i + 1] if np.isfinite(sp_open[i + 1]) else sp_close[i]
        nights = 3 if dates.iloc[i].weekday() == 4 else 1
        ret = _fill(1, entry, exit_, sp_close[i], s_out, slip) - swap_per_night * nights
        rows.append({"date": close.index[i], "trades": 1, "ret": ret, "worst": min(0.0, ret)})
    return pd.DataFrame(rows).set_index("date") if rows else _empty()
