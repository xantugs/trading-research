"""Features, labels and models for docs/ML_PLAN.md.

A decision row is one (date, half-hour checkpoint 10:00 ... 15:30 New York) of the traded
US index. Every feature uses only prices known at the checkpoint: the close of the minute
bar that ends at the checkpoint, earlier bars, and earlier days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .intraday_momentum import _fill

NY = "America/New_York"
OPEN, CLOSE = 570, 960
CHECKS = tuple(range(600, 931, 30))          # 10:00 ... 15:30
COLS = tuple(c - 1 - OPEN for c in CHECKS)   # grid column of the price at each checkpoint
LAST = CLOSE - 1 - OPEN                      # 15:59 bar close = the 16:00 price


def _rolling_prev(values: np.ndarray, window: int, min_periods: int, how: str = "mean") -> np.ndarray:
    frame = pd.DataFrame(values).rolling(window, min_periods=min_periods)
    out = frame.mean() if how == "mean" else frame.std()
    return out.shift(1).to_numpy().ravel()


def _asof(series: pd.Series, times: np.ndarray, tolerance_min: int = 10) -> np.ndarray:
    """Last value at or before each UTC time (ns int64), if no older than the tolerance."""
    index = series.index.asi8
    pos = np.searchsorted(index, times, side="right") - 1
    ok = (pos >= 0) & (times - index[np.clip(pos, 0, None)] <= tolerance_min * 60_000_000_000)
    out = np.full(len(times), np.nan)
    out[ok] = series.to_numpy()[pos[ok]]
    return out


def decision_frame(close: pd.DataFrame, first_open: pd.Series, other_close: pd.DataFrame,
                   other_open: pd.Series, dax: pd.Series, gold: pd.Series, market_flag: int) -> pd.DataFrame:
    """One row per (date, checkpoint) with features and forward labels.

    `close`/`first_open` are the traded market's 09:30-16:00 grid; `other_*` the other US
    index on the same dates; `dax` and `gold` are UTC minute-close series."""
    dates = close.index
    other_close = other_close.reindex(dates)
    other_open = other_open.reindex(dates)
    grid = close.to_numpy()
    opens = first_open.to_numpy()
    prev_close = pd.Series(grid[:, LAST]).shift(1).to_numpy()
    rets_1m = np.diff(grid, axis=1) / grid[:, :-1]

    # S2's noise area: 14-session average |move from the open| at each checkpoint
    dev = grid[:, list(COLS)] / opens[:, None] - 1
    sigma = pd.DataFrame(np.abs(dev)).rolling(14, min_periods=10).mean().shift(1).to_numpy()
    upper = np.maximum(opens, prev_close)[:, None] * (1 + sigma)
    lower = np.minimum(opens, prev_close)[:, None] * (1 - sigma)
    gap = opens / prev_close - 1
    gap_avg = _rolling_prev(np.abs(gap), 14, 10)

    # daily context (all from completed earlier days)
    day_close = grid[:, LAST]
    day_ret = pd.Series(day_close).pct_change().to_numpy()
    day_range = (np.nanmax(grid, axis=1) - np.nanmin(grid, axis=1)) / opens
    prev_ret = pd.Series(day_ret).shift(1).to_numpy()
    prev_range = pd.Series(day_range).shift(1).to_numpy()
    ret5 = (pd.Series(day_close).shift(1) / pd.Series(day_close).shift(6) - 1).to_numpy()
    ret20 = (pd.Series(day_close).shift(1) / pd.Series(day_close).shift(21) - 1).to_numpy()
    vol20 = pd.Series(day_ret).rolling(20, min_periods=15).std().shift(1).to_numpy()

    # intraday volatility so far, against its 20-day average at the same checkpoint
    vol_so_far = np.stack([np.nanstd(rets_1m[:, :c], axis=1) if c > 1 else np.full(len(dates), np.nan)
                           for c in COLS], axis=1)
    vol_avg = pd.DataFrame(vol_so_far).rolling(20, min_periods=15).mean().shift(1).to_numpy()

    times_utc = {}
    rows = []
    date_index = pd.DatetimeIndex(pd.to_datetime(pd.Series(dates)))
    for k, (check, col) in enumerate(zip(CHECKS, COLS)):
        stamp = (date_index + pd.Timedelta(minutes=check)).tz_localize(NY).tz_convert("UTC")
        times_utc[k] = stamp.asi8
        price = grid[:, col]

        def back(n: int) -> np.ndarray:
            return price / grid[:, col - n] - 1 if col - n >= 0 else np.full(len(dates), np.nan)

        o_grid = other_close.to_numpy()
        o_price = o_grid[:, col]
        o_back30 = o_price / o_grid[:, col - 30] - 1 if col >= 30 else np.full(len(dates), np.nan)
        t = times_utc[k]
        dax_now = _asof(dax, t)
        dax_30 = _asof(dax, t - 30 * 60_000_000_000)
        local_berlin = (date_index + pd.Timedelta(hours=9)).tz_localize("Europe/Berlin").tz_convert("UTC").asi8
        dax_open = _asof(dax, local_berlin + 60_000_000_000, tolerance_min=15)
        gold_now = _asof(gold, t)
        gold_30 = _asof(gold, t - 30 * 60_000_000_000)
        gold_60 = _asof(gold, t - 60 * 60_000_000_000)
        gold_820 = _asof(gold, (date_index + pd.Timedelta(minutes=500)).tz_localize(NY).tz_convert("UTC").asi8,
                         tolerance_min=15)
        fwd30 = grid[:, min(col + 30, LAST)] / price - 1
        fwd60 = grid[:, min(col + 60, LAST)] / price - 1
        fwd_close = grid[:, LAST] / price - 1
        span = grid[:, : col + 1]
        frame = pd.DataFrame({
            "date": dates, "check": k, "dow": date_index.dayofweek, "market": market_flag,
            "dev": dev[:, k], "dev_sigma": dev[:, k] / sigma[:, k],
            "to_upper": (price - upper[:, k]) / (opens * sigma[:, k]),
            "to_lower": (price - lower[:, k]) / (opens * sigma[:, k]),
            "s2_signal": np.where(price > upper[:, k], 1, np.where(price < lower[:, k], -1, 0)),
            "gap": gap, "gap_rel": np.abs(gap) / gap_avg,
            "r5": back(5), "r15": back(15), "r30": back(30), "r60": back(60),
            "since_prev_close": price / prev_close - 1,
            "vol_rel": vol_so_far[:, k] / vol_avg[:, k],
            "range_sigma": (np.nanmax(span, axis=1) - np.nanmin(span, axis=1)) / (opens * sigma[:, k]),
            "prev_ret": prev_ret, "prev_range": prev_range, "ret5d": ret5, "ret20d": ret20, "vol20d": vol20,
            "other_dev": o_price / other_open.to_numpy() - 1, "other_r30": o_back30,
            "dax_dev": dax_now / dax_open - 1, "dax_r30": dax_now / dax_30 - 1,
            "gold_dev": gold_now / gold_820 - 1, "gold_r30": gold_now / gold_30 - 1, "gold_r60": gold_now / gold_60 - 1,
            "price": price, "fwd30": fwd30, "fwd60": fwd60, "fwd_close": fwd_close,
        })
        rows.append(frame)
    out = pd.concat(rows, ignore_index=True)
    out = out[np.isfinite(out.price) & np.isfinite(out.dev_sigma)]
    return out.sort_values(["date", "check"]).reset_index(drop=True)


FEATURES = ("check", "dow", "market", "dev", "dev_sigma", "to_upper", "to_lower", "s2_signal", "gap", "gap_rel",
            "r5", "r15", "r30", "r60", "since_prev_close", "vol_rel", "range_sigma", "prev_ret", "prev_range",
            "ret5d", "ret20d", "vol20d", "other_dev", "other_r30", "dax_dev", "dax_r30", "gold_dev", "gold_r30",
            "gold_r60")


def trade_direct(frame: pd.DataFrame, prob_up: np.ndarray, theta: float, close: pd.DataFrame,
                 spread: pd.DataFrame, slip: float = 0.1) -> pd.DataFrame:
    """Project A's trading rule: position sign(P - 0.5) beyond theta at each checkpoint, trade only
    on changes, flat at 16:00. Returns one row per date with the net day return and trade count."""
    frame = frame.assign(p=prob_up)
    out = []
    for day, rows in frame.groupby("date", sort=True):
        if day not in close.index:
            continue
        row_close, row_spread = close.loc[day].to_numpy(), spread.loc[day].to_numpy()
        position, entry, s_entry, total, trades = 0, 0.0, 0.0, 0.0, 0
        for _, r in rows.sort_values("check").iterrows():
            col = COLS[int(r.check)]
            price, s_now = row_close[col], row_spread[col]
            if not np.isfinite(price):
                continue
            want = 1 if r.p - 0.5 > theta else (-1 if r.p - 0.5 < -theta else 0)
            if want != position:
                if position:
                    total += _fill(position, entry, price, s_entry, s_now, slip)
                position = want
                if position:
                    entry, s_entry, trades = price, s_now, trades + 1
        if position:
            # flat at 16:00, or at the last price of a shortened session (half-days close at 13:00)
            finite = np.flatnonzero(np.isfinite(row_close))
            last = LAST if np.isfinite(row_close[LAST]) else int(finite[-1])
            s_last = row_spread[last] if np.isfinite(row_spread[last]) else s_entry
            total += _fill(position, entry, row_close[last], s_entry, s_last, slip)
        out.append({"date": day, "trades": trades, "ret": total})
    return pd.DataFrame(out).set_index("date")
