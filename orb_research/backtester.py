"""Conservative M1 bid/ask path simulation for M5 close-confirmed signals."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .config import Config
from .risk import size_position, trade_commission
from .strategy import Signal, find_signals


def _spread(bar, config: Config) -> float:
    recorded = float(bar.spread) * config.point
    base = recorded if recorded > 0 else config.fallback_spread_price
    return base * config.spread_multiplier


def _stop_price(signal: Signal, entry: float, config: Config) -> float:
    if config.stop_method == "opposite_range":
        return signal.range_low if signal.direction == 1 else signal.range_high
    if config.stop_method == "range_multiple":
        width = signal.range_high - signal.range_low
        return (signal.range_high - width * config.stop_width_multiple
                if signal.direction == 1 else signal.range_low + width * config.stop_width_multiple)
    return entry - signal.direction * signal.prior_atr * config.atr_stop_multiple


def _exit_on_bar(bar, direction: int, stop: float, target: float, config: Config):
    spread = _spread(bar, config)
    if direction == 1:
        opening, high, low = float(bar.open), float(bar.high), float(bar.low)
        if opening <= stop:
            return opening - config.slippage_price, "stop_gap", False
        if opening >= target:
            return target - config.slippage_price, "target_gap", False
        stop_hit, target_hit = low <= stop, high >= target
        if stop_hit:
            return stop - config.slippage_price, "stop", target_hit
        if target_hit:
            return target - config.slippage_price, "target", False
    else:
        opening = float(bar.open) + spread
        high, low = float(bar.high) + spread, float(bar.low) + spread
        if opening >= stop:
            return opening + config.slippage_price, "stop_gap", False
        if opening <= target:
            return target + config.slippage_price, "target_gap", False
        stop_hit, target_hit = high >= stop, low <= target
        if stop_hit:
            return stop + config.slippage_price, "stop", target_hit
        if target_hit:
            return target + config.slippage_price, "target", False
    return None


def _excursion(bar, direction: int, entry: float, distance: float, config: Config):
    spread = _spread(bar, config)
    if direction == 1:
        adverse = max(0.0, entry - float(bar.low)) / distance
        favorable = max(0.0, float(bar.high) - entry) / distance
    else:
        adverse = max(0.0, float(bar.high) + spread - entry) / distance
        favorable = max(0.0, entry - (float(bar.low) + spread)) / distance
    return adverse, favorable


def _one_trade(m1: pd.DataFrame, signal: Signal, equity: float, config: Config):
    entry_time = signal.signal_close
    if entry_time not in m1.index:
        return None, "missing_entry_minute"
    first = m1.loc[entry_time]
    spread = _spread(first, config)
    entry = (float(first.open) + spread + config.slippage_price
             if signal.direction == 1 else float(first.open) - config.slippage_price)
    stop = _stop_price(signal, entry, config)
    distance = signal.direction * (entry - stop)
    if distance <= 0:
        return None, "invalid_stop_or_gap"
    target = entry + signal.direction * distance * config.reward_risk
    lots, estimated_risk = size_position(equity, entry, stop, config)
    if lots == 0:
        return None, "below_min_lot"

    zone = ZoneInfo(config.session_timezone)
    local_exit = datetime.combine(datetime.fromisoformat(signal.session_date).date(),
                                  datetime.strptime(config.session_exit, "%H:%M").time(), zone)
    exit_time_limit = pd.Timestamp(local_exit).tz_convert("UTC")
    path = m1.loc[(m1.index >= entry_time) & (m1.index <= exit_time_limit)]
    if path.empty or path.index[-1] < exit_time_limit:
        return None, "incomplete_exit_path"

    mae, mfe = 0.0, 0.0
    result = None
    for stamp, bar in path.iterrows():
        if stamp >= exit_time_limit:
            exit_price = (float(bar.open) - config.slippage_price if signal.direction == 1
                          else float(bar.open) + _spread(bar, config) + config.slippage_price)
            result = (stamp, exit_price, "time", False)
            break
        event = _exit_on_bar(bar, signal.direction, stop, target, config)
        if event is not None:
            exit_price, reason, ambiguous = event
            # We cannot know the complete path *after* an intraminute exit.
            if reason.startswith("stop"):
                mae = max(mae, max(0.0, signal.direction * (entry - exit_price)) / distance)
            else:
                mfe = max(mfe, max(0.0, signal.direction * (exit_price - entry)) / distance)
            result = (stamp, exit_price, reason, ambiguous)
            break
        adverse, favorable = _excursion(bar, signal.direction, entry, distance, config)
        mae, mfe = max(mae, adverse), max(mfe, favorable)
    if result is None:
        return None, "unresolved"
    exit_time, exit_price, exit_reason, ambiguous = result
    commission = trade_commission(lots, entry, exit_price, config)
    pnl = signal.direction * (exit_price - entry) * lots * config.contract_size - commission
    record = {
        "timestamp": entry_time, "session_date": signal.session_date,
        "session": signal.session, "direction": "long" if signal.direction == 1 else "short",
        "range_start": signal.range_start, "range_end": signal.range_end,
        "range_high": signal.range_high, "range_low": signal.range_low,
        "signal_open": signal.signal_open, "signal_close": signal.signal_close,
        "signal_price": signal.signal_price,
        "entry": entry, "stop": stop, "take_profit": target,
        "exit_time": exit_time, "exit": exit_price, "exit_reason": exit_reason,
        "position_size": lots, "risk_percent": config.risk_percent,
        "estimated_risk": estimated_risk, "R_result": pnl / estimated_risk,
        "PnL": pnl, "commission": commission, "MAE": mae, "MFE": mfe,
        "ambiguous_m1": ambiguous,
        "entry_spread": spread,
    }
    return record, None


def backtest(m1: pd.DataFrame, config: Config):
    signals, diagnostics = find_signals(m1, config)
    equity = config.initial_equity
    trades = []
    skipped = {}
    for signal in signals:
        record, reason = _one_trade(m1, signal, equity, config)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        equity += record["PnL"]
        record["equity_after"] = equity
        trades.append(record)
        if equity <= 0:
            raise RuntimeError("simulated account depleted; stop research")
    diagnostics["signals"] = len(signals)
    diagnostics["trades"] = len(trades)
    diagnostics["skipped"] = skipped
    return pd.DataFrame.from_records(trades), diagnostics
