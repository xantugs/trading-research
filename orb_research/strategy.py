"""A close-confirmed, one-trade-per-session M5 opening-range signal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import Config


@dataclass(frozen=True)
class Signal:
    session_date: str
    session: str
    direction: int
    range_start: pd.Timestamp
    range_end: pd.Timestamp
    range_high: float
    range_low: float
    signal_open: pd.Timestamp
    signal_close: pd.Timestamp
    signal_price: float
    prior_atr: float


def prior_daily_atr(m1: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR by UTC day, shifted so every session sees only completed days."""
    daily = m1.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    previous = daily.close.shift(1)
    tr = pd.concat([daily.high - daily.low, (daily.high - previous).abs(),
                    (daily.low - previous).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().shift(1)
    return atr


def breakout_buffer(config: Config, prior_atr: float) -> float:
    """Distance beyond the opening range a signal close must clear; the audit uses this too."""
    return config.breakout_buffer_atr * prior_atr if config.breakout_buffer_atr else 0.0


def signal_bars(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    grouped = m1.resample(rule, label="left", closed="left", origin="epoch")
    bars = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    bars["minutes_present"] = grouped.open.count()
    return bars.dropna(subset=["open"])


def find_signals(m1: pd.DataFrame, config: Config):
    """Return signals and diagnostics; bar close is never visible before bar end."""
    zone = ZoneInfo(config.session_timezone)
    m5 = signal_bars(m1, config.signal_minutes)
    atr = prior_daily_atr(m1)
    first_local = m1.index[0].tz_convert(zone).date()
    last_local = m1.index[-1].tz_convert(zone).date()
    records: list[Signal] = []
    counts = {"sessions": 0, "incomplete_range": 0, "no_breakout": 0,
              "incomplete_signal_bar": 0, "missing_atr": 0}
    for session_day in pd.date_range(first_local, last_local, freq="D"):
        day = session_day.date()
        if day.weekday() >= 5:
            continue
        local_open = datetime.combine(day, datetime.strptime(config.session_open, "%H:%M").time(), zone)
        local_exit = datetime.combine(day, datetime.strptime(config.session_exit, "%H:%M").time(), zone)
        start = pd.Timestamp(local_open).tz_convert("UTC")
        end = start + timedelta(minutes=config.range_minutes)
        cutoff = pd.Timestamp(local_exit).tz_convert("UTC")
        if start < m1.index[0] or cutoff > m1.index[-1]:
            continue
        counts["sessions"] += 1
        opening = m1.loc[(m1.index >= start) & (m1.index < end)]
        if len(opening) != config.range_minutes:
            counts["incomplete_range"] += 1
            continue
        high = float(opening.high.max())
        low = float(opening.low.min())
        if high <= low:
            counts["incomplete_range"] += 1
            continue
        day_key = start.floor("D")
        prior_atr = float(atr.get(day_key, np.nan))
        if (config.breakout_buffer_atr > 0 or config.stop_method == "atr") and not np.isfinite(prior_atr):
            counts["missing_atr"] += 1
            continue
        buffer = breakout_buffer(config, prior_atr)
        candidates = m5.loc[(m5.index >= end) &
                            (m5.index + timedelta(minutes=config.signal_minutes) <= cutoff)]
        found = False
        for bar_time, candle in candidates.iterrows():
            if candle.minutes_present != config.signal_minutes:
                counts["incomplete_signal_bar"] += 1
                continue
            direction = 1 if candle.close > high + buffer else -1 if candle.close < low - buffer else 0
            if not direction:
                continue
            records.append(Signal(
                session_date=day.isoformat(), session=config.session_name, direction=direction,
                range_start=start, range_end=end, range_high=high, range_low=low,
                signal_open=bar_time,
                signal_close=bar_time + timedelta(minutes=config.signal_minutes),
                signal_price=float(candle.close), prior_atr=prior_atr,
            ))
            found = True
            break
        if not found:
            counts["no_breakout"] += 1
    return records, counts
