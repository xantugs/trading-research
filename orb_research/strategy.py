"""Opening-range breakout signals: close-confirmed on an entry timeframe, or stop orders."""
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
    # Stop-order entries: the order price (fill is this or a worse gap open).  None for
    # close-confirmed signals, which fill at the next bar's open.
    stop_entry: float | None = None


ALLOWED_DIRECTIONS = {"both": (1, -1), "long": (1,), "short": (-1,)}


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


def minutes_since_range_end(signal: Signal) -> float:
    """Minutes between the end of the opening range and the confirming M5 close."""
    return (signal.signal_close - signal.range_end).total_seconds() / 60


def range_width_atr(signal: Signal) -> float:
    """Opening-range width in units of the prior daily ATR14 (the stop distance for opposite_range)."""
    return (signal.range_high - signal.range_low) / signal.prior_atr


def entry_rules_pass(signal: Signal, cutoff_minutes: float | None, min_width_atr: float | None) -> bool:
    """The signal-time rules exactly as find_signals applies them; used to post-filter scans."""
    if min_width_atr is not None and range_width_atr(signal) < min_width_atr:
        return False
    if cutoff_minutes is not None and minutes_since_range_end(signal) > cutoff_minutes:
        return False
    return True


def wilder_atr(bars: pd.DataFrame, period: int) -> pd.Series:
    """Wilder ATR on these bars (TradingView's ta.atr), labelled by bar open."""
    previous = bars.close.shift(1)
    tr = pd.concat([bars.high - bars.low, (bars.high - previous).abs(),
                    (bars.low - previous).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def spread_prices(m1: pd.DataFrame, config: Config) -> np.ndarray:
    """Per-minute modelled spread in price units, as the execution path applies it."""
    recorded = m1.spread.to_numpy(dtype=float) * config.point
    return np.where(recorded > 0, recorded, config.fallback_spread_price) * config.spread_multiplier


def signal_bars(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    grouped = m1.resample(rule, label="left", closed="left", origin="epoch")
    bars = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    bars["minutes_present"] = grouped.open.count()
    return bars.dropna(subset=["open"])


def find_signals(m1: pd.DataFrame, config: Config):
    """Return signals and diagnostics; bar close is never visible before bar end."""
    zone = ZoneInfo(config.session_timezone)
    entry_bars = signal_bars(m1, config.signal_minutes)
    atr = prior_daily_atr(m1)
    range_atr = (wilder_atr(signal_bars(m1, config.range_minutes), config.or_atr_period)
                 if config.or_min_atr is not None or config.or_max_atr is not None else None)
    stop_orders = config.entry_mode == "stop_order"
    if stop_orders:
        lows = m1.low.to_numpy(dtype=float)
        asks_high = m1.high.to_numpy(dtype=float) + spread_prices(m1, config)
    first_local = m1.index[0].tz_convert(zone).date()
    last_local = m1.index[-1].tz_convert(zone).date()
    records: list[Signal] = []
    counts = {"sessions": 0, "incomplete_range": 0, "no_breakout": 0,
              "incomplete_signal_bar": 0, "missing_atr": 0, "narrow_range": 0, "late_breakout": 0,
              "range_atr_filtered": 0, "range_candle_filtered": 0}
    needs_atr = (config.breakout_buffer_atr > 0 or config.stop_method == "atr"
                 or config.min_or_width_atr is not None or config.gap_max_atr is not None)
    trend = None
    if config.trend_sma_days is not None:
        # Prior completed day's close against the average of the N closes up to it.
        daily_close = m1.close.resample("1D").last().dropna()
        trend = pd.DataFrame({"close": daily_close.shift(1),
                              "sma": daily_close.rolling(config.trend_sma_days).mean().shift(1)})
    counts.update({"trend_filtered": 0, "gap_filtered": 0})
    # Held positions follow the EA cutoff hour: nothing that completes at the cutoff is traded.
    # Session-exit mode keeps the original rule (a close at the exit time is still a signal).
    held = config.position_exit != "session_exit"
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
        opening = m1.loc[start:end - timedelta(minutes=1)]
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
        if needs_atr and not np.isfinite(prior_atr):
            counts["missing_atr"] += 1
            continue
        if config.min_or_width_atr is not None and (high - low) / prior_atr < config.min_or_width_atr:
            counts["narrow_range"] += 1
            continue
        if range_atr is not None:
            intraday = float(range_atr.asof(start))
            if not np.isfinite(intraday):
                counts["missing_atr"] += 1
                continue
            if ((config.or_min_atr is not None and high - low < config.or_min_atr * intraday)
                    or (config.or_max_atr is not None and high - low > config.or_max_atr * intraday)):
                counts["range_atr_filtered"] += 1
                continue
        if config.gap_max_atr is not None:
            previous = day - timedelta(days=3 if day.weekday() == 0 else 1)
            prior_close_at = pd.Timestamp(datetime.combine(previous, datetime.strptime("16:00", "%H:%M").time(),
                                                           zone)).tz_convert("UTC") - timedelta(minutes=1)
            prior_close = float(m1.close.asof(prior_close_at)) if prior_close_at >= m1.index[0] else np.nan
            gap = abs(float(opening.open.iloc[0]) - prior_close)
            if not np.isfinite(gap) or gap > config.gap_max_atr * prior_atr:
                counts["gap_filtered"] += 1
                continue
        allowed_trend = None
        if trend is not None:
            row = trend.loc[day_key] if day_key in trend.index else None
            if row is None or not np.isfinite(row.sma) or not np.isfinite(row.close) or row.close == row.sma:
                counts["trend_filtered"] += 1
                continue
            allowed_trend = 1 if row.close > row.sma else -1
        if config.or_candle != "any":
            bullish = float(opening.close.iloc[-1]) > float(opening.open.iloc[0])
            if bullish != (config.or_candle == "bullish"):
                counts["range_candle_filtered"] += 1
                continue
        buffer = breakout_buffer(config, prior_atr)
        if stop_orders:
            candidates = _stop_order_touches(m1, lows, asks_high, end, cutoff, high + buffer,
                                             low - buffer)
        else:
            # The first entry bar evaluated is the first one that closes after the range is
            # complete; with an entry timeframe longer than the range, it also spans the range.
            first_bar = end - timedelta(minutes=config.signal_minutes - 1)
            last_bar = cutoff - timedelta(minutes=config.signal_minutes + held)
            window = entry_bars.loc[first_bar - timedelta(minutes=config.signal_minutes):last_bar]
            candidates = _close_breakouts(window, first_bar, high + buffer, low - buffer, config, counts)
        open_directions = set(ALLOWED_DIRECTIONS[config.trade_direction])
        if allowed_trend is not None:
            open_directions &= {allowed_trend}
        found = False
        for bar_time, direction, price in candidates:
            if direction not in open_directions:
                continue
            signal = Signal(
                session_date=day.isoformat(), session=config.session_name, direction=direction,
                range_start=start, range_end=end, range_high=high, range_low=low,
                signal_open=bar_time,
                signal_close=(bar_time if stop_orders
                              else bar_time + timedelta(minutes=config.signal_minutes)),
                signal_price=price, prior_atr=prior_atr,
                stop_entry=price if stop_orders else None,
            )
            found = True
            # Only the first breakout counts (per direction under one_per_direction; every fresh
            # one under after_close, where execution decides); a late one means no trade for it.
            if entry_rules_pass(signal, config.entry_cutoff_minutes_after_or, None):
                records.append(signal)
            else:
                counts["late_breakout"] += 1
            if config.trades_per_session == "one_per_direction":
                open_directions.discard(direction)
            elif config.trades_per_session == "one":
                open_directions.clear()
            if not open_directions:
                break
        if not found:
            counts["no_breakout"] += 1
    return records, counts


def _close_breakouts(window: pd.DataFrame, first_bar, upper: float, lower: float, config: Config,
                     counts: dict):
    """Entry bars that close beyond the range, in time order, as (open time, direction, close).

    Under after_close only fresh breakouts count (the previous entry bar closed inside), so a
    position stopped out while price stays outside is not re-entered on the same move.
    """
    previous = None
    for bar_time, candle in window.iterrows():
        prior, previous = previous, float(candle.close)
        if bar_time < first_bar:
            continue
        if candle.minutes_present != config.signal_minutes:
            counts["incomplete_signal_bar"] += 1
            continue
        close = float(candle.close)
        direction = 1 if close > upper else -1 if close < lower else 0
        if not direction:
            continue
        if (config.trades_per_session == "after_close" and prior is not None
                and (prior > upper if direction == 1 else prior < lower)):
            continue
        yield bar_time, direction, close


def _stop_order_touches(m1: pd.DataFrame, lows, asks_high, end, cutoff, upper: float, lower: float):
    """Minutes before the cutoff whose ask high reaches the buy stop or bid low the sell stop.

    A minute that reaches both yields the long first, as the EA checks long before short.
    """
    left, right = m1.index.searchsorted(end), m1.index.searchsorted(cutoff)
    longs = asks_high[left:right] >= upper
    shorts = lows[left:right] <= lower
    for offset in np.flatnonzero(longs | shorts):
        stamp = m1.index[left + offset]
        if longs[offset]:
            yield stamp, 1, upper
        if shorts[offset]:
            yield stamp, -1, lower
