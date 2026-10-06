"""Causal, pre-entry features for frozen ORB candidate events."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategy import Signal, signal_bars


FEATURE_COLUMNS = (
    "direction_sign", "breakout_buffer_atr", "or_high", "or_low", "or_width_price",
    "or_width_pct", "or_width_atr", "or_return", "or_body_direction", "or_close_location",
    "atr_at_entry", "atr_relative_to_price", "breakout_distance_price",
    "breakout_strength_atr", "breakout_candle_range_atr", "breakout_body_atr",
    "breakout_body_ratio", "breakout_close_location", "breakout_wick_against_direction_atr",
    "breakout_wick_with_direction_atr", "minutes_since_or_end", "minutes_since_session_open",
    "entry_hour_ny", "entry_minute_ny", "day_of_week", "pre_or_return_30m",
    "pre_or_return_60m", "pre_or_return_120m", "return_from_session_open",
    "distance_from_session_open_atr", "realized_volatility_before_entry",
    "price_vs_ema20_atr", "price_vs_ema50_atr", "ema20_slope_atr",
)


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if np.isfinite(denominator) and denominator > 0 else np.nan


def _complete_window(m1: pd.DataFrame, end_exclusive: pd.Timestamp, minutes: int) -> pd.DataFrame:
    """Exactly `minutes` bars ending one minute before `end_exclusive`."""
    start = end_exclusive - pd.Timedelta(minutes=minutes)
    bars = m1.loc[start:end_exclusive - pd.Timedelta(minutes=1)]
    if (len(bars) != minutes or bars.index[0] != start or
            bars.index[-1] != end_exclusive - pd.Timedelta(minutes=1) or
            not (bars.index.to_series().diff().iloc[1:] == pd.Timedelta(minutes=1)).all()):
        return m1.iloc[0:0]
    return bars


@dataclass
class FeatureContext:
    m1: pd.DataFrame
    signal_minutes: int

    def __post_init__(self) -> None:
        complete = signal_bars(self.m1, self.signal_minutes)
        self.m5 = complete.loc[complete.minutes_present == self.signal_minutes].copy()
        closes = self.m5.close
        self.m5["ema20"] = closes.ewm(span=20, adjust=False, min_periods=20).mean()
        self.m5["ema50"] = closes.ewm(span=50, adjust=False, min_periods=50).mean()

    def snapshot(self, signal: Signal, buffer: float) -> tuple[dict, dict]:
        """Return values and the latest information timestamp for every feature."""
        t = signal.signal_close
        if (t - pd.Timedelta(minutes=1) > self.m1.index[-1] or
                signal.signal_open + pd.Timedelta(minutes=self.signal_minutes) != t):
            raise ValueError("signal is outside the available completed M1 history")
        opening = _complete_window(self.m1, signal.range_end, int((signal.range_end - signal.range_start).total_seconds() / 60))
        breakout = _complete_window(self.m1, t, self.signal_minutes)
        if opening.empty or breakout.empty or signal.signal_open != breakout.index[0]:
            raise ValueError("opening range or breakout candle is incomplete")
        if not np.isclose(float(opening.high.max()), signal.range_high) or not np.isclose(float(opening.low.min()), signal.range_low):
            raise ValueError("signal opening range disagrees with raw M1 history")
        candle = self.m5.loc[signal.signal_open]
        if not np.isclose(float(candle.close), signal.signal_price):
            raise ValueError("signal close disagrees with completed M5 bar")
        or_open, or_close = float(opening.open.iloc[0]), float(opening.close.iloc[-1])
        width, atr = signal.range_high - signal.range_low, signal.prior_atr
        body = float(candle.close - candle.open)
        candle_range = float(candle.high - candle.low)
        upper_wick = float(candle.high - max(candle.open, candle.close))
        lower_wick = float(min(candle.open, candle.close) - candle.low)
        distance = (float(candle.close) - signal.range_high if signal.direction == 1
                    else signal.range_low - float(candle.close))
        if distance <= 0:
            raise ValueError("candidate is not beyond its opening-range boundary")
        local = t.tz_convert("America/New_York")
        values = {
            "direction_sign": signal.direction, "breakout_buffer_atr": buffer,
            "or_high": signal.range_high, "or_low": signal.range_low,
            "or_width_price": width, "or_width_pct": _ratio(width, or_open),
            "or_width_atr": _ratio(width, atr), "or_return": or_close / or_open - 1,
            "or_body_direction": int(np.sign(or_close - or_open)),
            "or_close_location": _ratio(or_close - signal.range_low, width),
            "atr_at_entry": atr, "atr_relative_to_price": _ratio(atr, signal.signal_price),
            "breakout_distance_price": distance, "breakout_strength_atr": _ratio(distance, atr),
            "breakout_candle_range_atr": _ratio(candle_range, atr),
            "breakout_body_atr": _ratio(abs(body), atr),
            "breakout_body_ratio": _ratio(abs(body), candle_range),
            "breakout_close_location": (_ratio(float(candle.close - candle.low), candle_range)
                                        if signal.direction == 1 else
                                        _ratio(float(candle.high - candle.close), candle_range)),
            "breakout_wick_against_direction_atr": _ratio(lower_wick if signal.direction == 1 else upper_wick, atr),
            "breakout_wick_with_direction_atr": _ratio(upper_wick if signal.direction == 1 else lower_wick, atr),
            "minutes_since_or_end": (t - signal.range_end).total_seconds() / 60,
            "minutes_since_session_open": (t - signal.range_start).total_seconds() / 60,
            "entry_hour_ny": local.hour, "entry_minute_ny": local.minute,
            "day_of_week": local.weekday(),
            "return_from_session_open": signal.signal_price / or_open - 1,
            "distance_from_session_open_atr": (signal.signal_price - or_open) / atr,
        }
        sources = {name: t for name in values}
        sources["breakout_buffer_atr"] = signal.range_start
        for name in ("or_high", "or_low", "or_width_price", "or_width_pct", "or_width_atr",
                     "or_return", "or_body_direction", "or_close_location"):
            sources[name] = signal.range_end
        sources["atr_at_entry"] = signal.range_start.floor("D")
        sources["atr_relative_to_price"] = t
        for minutes in (30, 60, 120):
            name = f"pre_or_return_{minutes}m"
            window = _complete_window(self.m1, signal.range_start, minutes + 1)
            values[name] = float(window.close.iloc[-1] / window.close.iloc[0] - 1) if not window.empty else np.nan
            sources[name] = signal.range_start
        vol_window = _complete_window(self.m1, t, 61)
        values["realized_volatility_before_entry"] = (
            float(np.log(vol_window.close).diff().iloc[1:].std(ddof=1)) if not vol_window.empty else np.nan
        )
        sources["realized_volatility_before_entry"] = t
        history = self.m5.loc[:signal.signal_open]
        last = history.iloc[-1]
        values["price_vs_ema20_atr"] = ((signal.signal_price - float(last.ema20)) / atr
                                            if np.isfinite(last.ema20) else np.nan)
        values["price_vs_ema50_atr"] = ((signal.signal_price - float(last.ema50)) / atr
                                            if np.isfinite(last.ema50) else np.nan)
        values["ema20_slope_atr"] = ((float(last.ema20) - float(history.iloc[-2].ema20)) / atr
                                     if len(history) >= 2 and np.isfinite(last.ema20) and
                                     np.isfinite(history.iloc[-2].ema20) else np.nan)
        for name in ("price_vs_ema20_atr", "price_vs_ema50_atr", "ema20_slope_atr"):
            sources[name] = t
        if set(values) != set(FEATURE_COLUMNS) or set(sources) != set(FEATURE_COLUMNS):
            raise AssertionError("feature list and source timestamps disagree")
        if any(source > t for source in sources.values()) or opening.index[-1] >= t or breakout.index[-1] >= t:
            raise AssertionError("post-signal market data reached a feature")
        return values, sources
