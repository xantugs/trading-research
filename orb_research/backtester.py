"""Conservative M1 bid/ask path simulation for opening-range breakout signals."""
from __future__ import annotations

import heapq
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import Config
from .risk import size_position, trade_commission
from .strategy import Signal, find_signals, signal_bars, spread_prices, wilder_atr


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


class PathArrays:
    """Column arrays shared by every trade of one simulation, built once per M1 frame."""

    def __init__(self, m1: pd.DataFrame, config: Config):
        self.index = m1.index
        self.open = m1.open.to_numpy(dtype=float)
        self.high = m1.high.to_numpy(dtype=float)
        self.low = m1.low.to_numpy(dtype=float)
        self.spread = spread_prices(m1, config)
        self.trail_close = self.trail_atr = None
        if config.trail_atr_multiple is not None:
            # Each entry bar's close and ATR, placed on the M1 row of its last minute: the stop
            # is re-evaluated as that bar closes and applies from the next minute.
            bars = signal_bars(m1, config.signal_minutes)
            atr = wilder_atr(bars, config.trail_atr_period).to_numpy()
            last = m1.index.searchsorted(bars.index + pd.Timedelta(minutes=config.signal_minutes)) - 1
            self.trail_close = np.full(len(m1), np.nan)
            self.trail_atr = np.full(len(m1), np.nan)
            self.trail_close[last] = bars.close.to_numpy()
            self.trail_atr[last] = atr


def _flat_time_limit(signal: Signal, config: Config) -> pd.Timestamp | None:
    """When an open position is closed at market regardless of stop or target."""
    if config.position_exit == "stop_or_target":
        return None
    zone = ZoneInfo(config.session_timezone)
    day = datetime.fromisoformat(signal.session_date).date()
    if config.position_exit == "session_exit":
        label = config.session_exit
    else:
        label = config.flat_time
        if config.position_exit == "friday_close":
            day = day + pd.Timedelta(days=4 - day.weekday())
    local = datetime.combine(day, datetime.strptime(label, "%H:%M").time(), zone)
    return pd.Timestamp(local).tz_convert("UTC")


def _time_exit(arrays: PathArrays, i: int, direction: int, config: Config) -> float:
    return (arrays.open[i] - config.slippage_price if direction == 1
            else arrays.open[i] + arrays.spread[i] + config.slippage_price)


def _trail_until_exit(arrays: PathArrays, start: int, limit: int | None, direction: int, entry: float,
                      stop: float, distance: float, config: Config):
    """Walk M1 bars with an ATR trailing stop and no fixed target.

    The stop is checked first on every bar (gap opens fill at the open).  Once a bar reaches
    trail_activation_r × the initial risk, every later entry-bar close moves the stop to
    close ∓ trail_atr_multiple × ATR if that is tighter; it never loosens.
    """
    activation = entry + direction * config.trail_activation_r * distance
    active = config.trail_activation_r == 0
    initial = stop
    mae = mfe = 0.0
    end = len(arrays.open) if limit is None else min(limit, len(arrays.open))
    for i in range(start, end):
        spread = 0.0 if direction == 1 else arrays.spread[i]
        opening, high, low = arrays.open[i] + spread, arrays.high[i] + spread, arrays.low[i] + spread
        moved = stop != initial
        if direction * (opening - stop) <= 0:
            return (arrays.index[i], opening - direction * config.slippage_price,
                    "trail_stop_gap" if moved else "stop_gap", False, mae, mfe), None
        adverse = (entry - low) if direction == 1 else (high - entry)
        favorable = (high - entry) if direction == 1 else (entry - low)
        if (low <= stop) if direction == 1 else (high >= stop):
            mae = max(mae, max(0.0, direction * (entry - stop)) / distance)
            mfe = max(mfe, max(0.0, favorable) / distance)
            return (arrays.index[i], stop - direction * config.slippage_price,
                    "trail_stop" if moved else "stop", False, mae, mfe), None
        mae, mfe = max(mae, max(0.0, adverse) / distance), max(mfe, max(0.0, favorable) / distance)
        if not active and ((high >= activation) if direction == 1 else (low <= activation)):
            active = True
        if active and np.isfinite(arrays.trail_atr[i]):
            close = arrays.trail_close[i] + spread
            candidate = close - direction * config.trail_atr_multiple * arrays.trail_atr[i]
            stop = max(stop, candidate) if direction == 1 else min(stop, candidate)
    if limit is not None and limit < len(arrays.open):
        return (arrays.index[limit], _time_exit(arrays, limit, direction, config), "time", False,
                mae, mfe), None
    return None, "open_at_data_end"


def _breakeven_until_exit(arrays: PathArrays, start: int, limit: int | None, direction: int,
                          entry: float, stop: float, target: float, distance: float, config: Config):
    """Walk M1 bars with a fixed target and a stop that moves to the entry price once a bar
    reaches breakeven_r x the initial risk.  Within a bar the stop and target are checked
    first (stop before target, as everywhere else); the move applies from the next bar.
    """
    trigger = entry + direction * config.breakeven_r * distance
    moved = False
    mae = mfe = 0.0
    slip = config.slippage_price
    end = len(arrays.open) if limit is None else min(limit, len(arrays.open))
    for i in range(start, end):
        spread = 0.0 if direction == 1 else arrays.spread[i]
        opening, high, low = arrays.open[i] + spread, arrays.high[i] + spread, arrays.low[i] + spread
        reason = "breakeven_stop" if moved else "stop"
        if direction * (opening - stop) <= 0:
            return (arrays.index[i], opening - direction * slip, reason + "_gap", False, mae, mfe), None
        if direction * (opening - target) >= 0:
            return (arrays.index[i], target - direction * slip, "target_gap", False, mae, mfe), None
        favorable = (high - entry) if direction == 1 else (entry - low)
        adverse = (entry - low) if direction == 1 else (high - entry)
        stop_hit = (low <= stop) if direction == 1 else (high >= stop)
        target_hit = (high >= target) if direction == 1 else (low <= target)
        if stop_hit:
            mae = max(mae, max(0.0, direction * (entry - stop)) / distance)
            return (arrays.index[i], stop - direction * slip, reason, target_hit, mae, mfe), None
        if target_hit:
            return (arrays.index[i], target - direction * slip, "target", False, mae,
                    max(mfe, max(0.0, favorable) / distance)), None
        mae, mfe = max(mae, max(0.0, adverse) / distance), max(mfe, max(0.0, favorable) / distance)
        if not moved and ((high >= trigger) if direction == 1 else (low <= trigger)):
            stop, moved = entry, True
    if limit is not None and limit < len(arrays.open):
        return (arrays.index[limit], _time_exit(arrays, limit, direction, config), "time", False,
                mae, mfe), None
    return None, "open_at_data_end"


def _exit_by_session_end(m1: pd.DataFrame, signal: Signal, entry_time, entry: float, stop: float,
                         target: float, distance: float, config: Config):
    """Walk M1 bars until stop, target or the session_exit flat time."""
    exit_time_limit = _flat_time_limit(signal, config)
    path = m1.loc[entry_time:exit_time_limit]
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
    return (*result, mae, mfe), None


def _hold_until_exit(arrays: PathArrays, m1: pd.DataFrame, start: int, limit: int | None,
                     direction: int, entry: float, stop: float, target: float, distance: float,
                     config: Config, chunk: int = 4096):
    """Run a position past the session, across nights and weekends, to its stop or target.

    Bars are scanned in vectorised chunks for the first one that would trigger an exit;
    that bar is then resolved by _exit_on_bar, so fills, gaps and the sl_first rule are
    identical to the session-exit path.  With a flat-time limit the position is closed at
    that bar's open if neither level was reached first.  A position still open when the
    data ends is skipped.
    """
    mae, mfe = 0.0, 0.0
    end = len(m1) if limit is None else min(limit, len(m1))
    for left in range(start, end, chunk):
        right = min(left + chunk, end)
        if direction == 1:
            op, hi, lo = arrays.open[left:right], arrays.high[left:right], arrays.low[left:right]
            hits = (op <= stop) | (op >= target) | (lo <= stop) | (hi >= target)
        else:
            spread = arrays.spread[left:right]
            op = arrays.open[left:right] + spread
            hi, lo = arrays.high[left:right] + spread, arrays.low[left:right] + spread
            hits = (op >= stop) | (op <= target) | (hi >= stop) | (lo <= target)
        found = np.flatnonzero(hits)
        stop_at = int(found[0]) if len(found) else right - left
        if stop_at:
            if direction == 1:
                adverse = np.maximum(0.0, entry - lo[:stop_at]).max() / distance
                favorable = np.maximum(0.0, hi[:stop_at] - entry).max() / distance
            else:
                adverse = np.maximum(0.0, hi[:stop_at] - entry).max() / distance
                favorable = np.maximum(0.0, entry - lo[:stop_at]).max() / distance
            mae, mfe = max(mae, float(adverse)), max(mfe, float(favorable))
        if len(found):
            stamp = m1.index[left + stop_at]
            exit_price, reason, ambiguous = _exit_on_bar(m1.iloc[left + stop_at], direction, stop,
                                                         target, config)
            if reason.startswith("stop"):
                mae = max(mae, max(0.0, direction * (entry - exit_price)) / distance)
            else:
                mfe = max(mfe, max(0.0, direction * (exit_price - entry)) / distance)
            return (stamp, exit_price, reason, ambiguous, mae, mfe), None
    if limit is not None and limit < len(m1):
        return (m1.index[limit], _time_exit(arrays, limit, direction, config), "time", False,
                mae, mfe), None
    return None, "open_at_data_end"


def financing_nights(entry_time: pd.Timestamp, exit_time: pd.Timestamp) -> int:
    """17:00 New York rollovers between entry and exit, in calendar nights (a weekend is 3)."""
    def rollover_day(stamp):
        return (stamp.tz_convert("America/New_York") - pd.Timedelta(hours=17)).date()

    return (rollover_day(exit_time) - rollover_day(entry_time)).days


def _one_trade(m1: pd.DataFrame, signal: Signal, equity: float, config: Config,
               arrays: PathArrays | None = None):
    entry_time = signal.signal_close
    if entry_time not in m1.index:
        return None, "missing_entry_minute"
    arrays = arrays or PathArrays(m1, config)
    position = m1.index.get_loc(entry_time)
    first = m1.iloc[position]
    spread = _spread(first, config)
    if signal.stop_entry is None:
        entry = (float(first.open) + spread + config.slippage_price
                 if signal.direction == 1 else float(first.open) - config.slippage_price)
    elif signal.direction == 1:  # buy stop: the order price, or the ask open if it gapped through
        entry = max(float(first.open) + spread, signal.stop_entry) + config.slippage_price
    else:
        entry = min(float(first.open), signal.stop_entry) - config.slippage_price
    stop = _stop_price(signal, entry, config)
    distance = signal.direction * (entry - stop)
    if distance <= 0:
        return None, "invalid_stop_or_gap"
    trailing = config.trail_atr_multiple is not None
    target = entry + signal.direction * distance * (config.trail_activation_r if trailing
                                                    else config.reward_risk)
    lots, estimated_risk = size_position(equity, entry, stop, config)
    if lots == 0:
        return None, "below_min_lot"

    start = position
    result = None
    if signal.stop_entry is not None:
        # A stop order fills inside its minute; whether the rest of that minute came before or
        # after the fill is unknown, so the entry minute can only stop the trade out, never
        # reach the target (the conservative reading).
        touched = (float(first.low) <= stop if signal.direction == 1
                   else float(first.high) + spread >= stop)
        if touched:
            result = (entry_time, stop - signal.direction * config.slippage_price, "stop", True,
                      1.0, 0.0)
        start = position + 1
    if result is None:
        limit_time = _flat_time_limit(signal, config)
        if config.position_exit == "session_exit" and not trailing and config.breakeven_r is None:
            if start >= len(m1):
                return None, "incomplete_exit_path"
            result, reason = _exit_by_session_end(m1, signal, m1.index[start], entry, stop, target,
                                                  distance, config)
        else:
            limit = None if limit_time is None else int(m1.index.searchsorted(limit_time))
            if trailing:
                result, reason = _trail_until_exit(arrays, start, limit, signal.direction, entry,
                                                   stop, distance, config)
            elif config.breakeven_r is not None:
                result, reason = _breakeven_until_exit(arrays, start, limit, signal.direction, entry,
                                                       stop, target, distance, config)
            else:
                result, reason = _hold_until_exit(arrays, m1, start, limit, signal.direction, entry,
                                                  stop, target, distance, config)
        if result is None:
            return None, reason
    exit_time, exit_price, exit_reason, ambiguous, mae, mfe = result
    commission = trade_commission(lots, entry, exit_price, config)
    financing = (entry * lots * config.contract_size * config.financing_pct_annual / 100 / 365
                 * financing_nights(entry_time, exit_time)) if config.financing_pct_annual else 0.0
    pnl = (signal.direction * (exit_price - entry) * lots * config.contract_size
           - commission - financing)
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
        "PnL": pnl, "commission": commission, "financing": financing, "MAE": mae, "MFE": mfe,
        "ambiguous_m1": ambiguous,
        "entry_spread": spread,
    }
    return record, None


def minimum_lot_feasibility(m1: pd.DataFrame, signals: list[Signal], config: Config,
                            *, equity: float = 5000.0) -> dict:
    """Counterfactual minimum-lot check; never removes research signals.

    Uses the same next-M1-open entry, spread, stop and risk sizing as the
    execution path. Missing entry bars and invalid gap stops are counted
    separately, rather than mislabelled as minimum-lot failures.
    """
    infeasible = valid = invalid = 0
    for signal in signals:
        if signal.signal_close not in m1.index:
            invalid += 1
            continue
        bar = m1.loc[signal.signal_close]
        entry = (float(bar.open) + _spread(bar, config) + config.slippage_price
                 if signal.direction == 1 else float(bar.open) - config.slippage_price)
        stop = _stop_price(signal, entry, config)
        if signal.direction * (entry - stop) <= 0:
            invalid += 1
            continue
        valid += 1
        lots, _ = size_position(equity, entry, stop, config)
        infeasible += lots < config.min_lot
    return {"signals": len(signals), "valid_sizing_signals": valid,
            "invalid_entry_or_gap": invalid, "infeasible_at_5k": infeasible,
            "infeasible_share": infeasible / valid if valid else None}


def _floating_pnl(bar, position: dict, config: Config) -> float:
    """Open profit of a position marked at this bar's open (bid for longs, ask for shorts)."""
    close = float(bar.open) if position["direction"] == 1 else float(bar.open) + _spread(bar, config)
    return position["direction"] * (close - position["entry"]) * position["lots"] * config.contract_size


def simulate_signals(m1: pd.DataFrame, signals: list[Signal], config: Config,
                     initial_equity: float | None = None):
    """Replay selected signals with a fresh or carried-forward equity balance.

    Like the MT5 EA, each trade is sized from account equity at its entry minute:
    the balance of every trade closed before then plus the open profit of positions
    still running.  When positions never overlap this is the balance after the
    previous trade, as before.
    """
    start = config.initial_equity if initial_equity is None else initial_equity
    balance = start
    trades = []
    skipped = {}
    open_positions: list[tuple[pd.Timestamp, int, dict]] = []
    arrays = PathArrays(m1, config) if signals else None
    session_busy_until: dict[str, pd.Timestamp] = {}
    for signal in signals:
        # after_close: a session holds at most one position at a time; a breakout while it is
        # still open (including one closing in the entry minute) is not taken.
        busy_until = session_busy_until.get(signal.session_date)
        if busy_until is not None and busy_until >= signal.signal_close:
            skipped["session_position_open"] = skipped.get("session_position_open", 0) + 1
            continue
        while open_positions and open_positions[0][0] < signal.signal_close:
            balance += heapq.heappop(open_positions)[2]["PnL"]
        equity = balance
        if open_positions and signal.signal_close in m1.index:
            bar = m1.loc[signal.signal_close]
            equity += sum(_floating_pnl(bar, record, config) for _, _, record in open_positions)
        if equity <= 0:
            raise RuntimeError("simulated account depleted; stop research")
        record, reason = _one_trade(m1, signal, equity, config, arrays)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        if config.trades_per_session == "after_close":
            session_busy_until[signal.session_date] = record["exit_time"]
        heapq.heappush(open_positions, (record["exit_time"], len(trades),
                                        {"PnL": record["PnL"], "direction": signal.direction,
                                         "entry": record["entry"], "lots": record["position_size"]}))
        trades.append(record)
    frame = pd.DataFrame.from_records(trades)
    if not frame.empty:
        booked = frame.sort_values("exit_time", kind="stable")
        frame["equity_after"] = start + booked.PnL.cumsum()
        if (frame.equity_after <= 0).any():
            raise RuntimeError("simulated account depleted; stop research")
    return frame, {
        "signals": len(signals), "trades": len(trades), "skipped": skipped,
    }


def backtest(m1: pd.DataFrame, config: Config):
    signals, diagnostics = find_signals(m1, config)
    trades, execution = simulate_signals(m1, signals, config)
    diagnostics.update(execution)
    return trades, diagnostics
