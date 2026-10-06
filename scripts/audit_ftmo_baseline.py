#!/usr/bin/env python3
"""Check representative baseline trades directly against cached FTMO minute bars."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import load_m1
from orb_research.strategy import breakout_buffer, prior_daily_atr, signal_bars


def audit(trade: pd.Series, bars: pd.DataFrame, config, atr: pd.Series) -> dict:
    start = pd.Timestamp(trade.range_start)
    end = pd.Timestamp(trade.range_end)
    signal_open = pd.Timestamp(trade.signal_open)
    entry_time = pd.Timestamp(trade.timestamp)
    exit_time = pd.Timestamp(trade.exit_time)
    opening = bars.loc[(bars.index >= start) & (bars.index < end)]
    assert len(opening) == int((end - start).total_seconds() / 60)
    assert trade.range_high == opening.high.max()
    assert trade.range_low == opening.low.min()
    prior_atr = float(atr.get(start.floor("D"), np.nan))
    if config.breakout_buffer_atr > 0 or config.stop_method == "atr":
        assert np.isfinite(prior_atr)
    buffer = breakout_buffer(config, prior_atr)
    upper_trigger = trade.range_high + buffer
    lower_trigger = trade.range_low - buffer

    stop_order = config.entry_mode == "stop_order"
    trailing = config.trail_atr_multiple is not None
    opposite_closed = config.trades_per_session == "one" and config.trade_direction == "both"
    first = bars.loc[entry_time]
    recorded = float(first.spread) * config.point
    spread = (recorded if recorded > 0 else config.fallback_spread_price) * config.spread_multiplier
    if stop_order:
        # The fill minute is the first since the range ended whose ask high (long) or bid low
        # (short) reached the order; earlier minutes did not (in either direction under "one").
        assert entry_time == signal_open
        level = upper_trigger if trade.direction == "long" else lower_trigger
        assert trade.signal_price == level
        before = bars.loc[(bars.index >= end) & (bars.index < entry_time)]
        raw = before.spread.to_numpy(dtype=float) * config.point
        asks_high = before.high.to_numpy() + np.where(raw > 0, raw, config.fallback_spread_price) \
            * config.spread_multiplier
        if trade.direction == "long" or opposite_closed:
            assert (asks_high < upper_trigger).all()
        if trade.direction == "short" or opposite_closed:
            assert (before.low.to_numpy() > lower_trigger).all()
        assert (float(first.high) + spread >= level if trade.direction == "long"
                else float(first.low) <= level)
        expected_entry = (max(float(first.open) + spread, level) + config.slippage_price
                          if trade.direction == "long"
                          else min(float(first.open), level) - config.slippage_price)
    else:
        _audit_close_signal(trade, bars, config, end, signal_open, entry_time, upper_trigger,
                            lower_trigger, opposite_closed)
        expected_entry = (float(first.open) + spread + config.slippage_price
                          if trade.direction == "long"
                          else float(first.open) - config.slippage_price)
    assert abs(trade.entry - expected_entry) < 1e-7
    _audit_exit(trade, bars, config, prior_atr, entry_time, exit_time, stop_order, trailing)
    return {"date": trade.session_date, "direction": trade.direction,
            "signal_utc": str(entry_time), "exit_utc": str(exit_time),
            "exit_reason": trade.exit_reason, "net_R": round(float(trade.R_result), 4),
            "breakout_buffer": round(buffer, 4), "status": "passed"}


def _audit_close_signal(trade, bars, config, end, signal_open, entry_time, upper_trigger,
                        lower_trigger, opposite_closed) -> None:
    # Earlier complete entry bars must not have broken out in this trade's direction; under
    # one trade per session with both directions allowed, not in the other direction either.
    # after_close re-entries follow earlier trades of the same session, so only the
    # freshness of their own breakout (previous entry bar inside) is checked.
    first_bar = end - pd.Timedelta(minutes=config.signal_minutes - 1)
    window = bars.loc[(bars.index >= first_bar.floor(f"{config.signal_minutes}min"))
                      & (bars.index < signal_open)]
    earlier = signal_bars(window, config.signal_minutes) if not window.empty else window
    earlier = earlier.loc[(earlier.index >= first_bar)
                          & (earlier.minutes_present == config.signal_minutes)]
    if config.trades_per_session == "after_close":
        earlier = earlier.tail(1)
    for close in earlier.close:
        if trade.direction == "long" or opposite_closed:
            assert close <= upper_trigger
        if trade.direction == "short" or opposite_closed:
            assert close >= lower_trigger
    signal = bars.loc[(bars.index >= signal_open) & (bars.index < entry_time)]
    assert len(signal) == config.signal_minutes
    assert trade.signal_price == signal.close.iloc[-1]
    assert ((trade.signal_price > upper_trigger) if trade.direction == "long"
            else (trade.signal_price < lower_trigger))


def _audit_exit(trade, bars, config, prior_atr, entry_time, exit_time, stop_order, trailing) -> None:
    if config.stop_method == "opposite_range":
        assert trade.stop == (trade.range_low if trade.direction == "long" else trade.range_high)
    elif config.stop_method == "range_multiple":
        width = trade.range_high - trade.range_low
        expected_stop = (trade.range_high - width * config.stop_width_multiple
                         if trade.direction == "long" else
                         trade.range_low + width * config.stop_width_multiple)
        assert abs(trade.stop - expected_stop) < 1e-7
    elif config.stop_method == "atr":
        sign = 1 if trade.direction == "long" else -1
        expected_stop = trade.entry - sign * prior_atr * config.atr_stop_multiple
        assert abs(trade.stop - expected_stop) < 1e-7
    # With a trailing stop, take_profit records the activation level, not an exit.
    expected_target = trade.entry + (trade.entry - trade.stop) * (
        config.trail_activation_r if trailing else config.reward_risk)
    assert abs(trade.take_profit - expected_target) < 1e-7
    assert trade.exit_time >= trade.timestamp
    assert exit_time in bars.index

    # A trailing stop moves, so only its exit bar is checked; a stop-order fill minute can only
    # stop out, so it is excluded from the no-touch path.
    first_path = entry_time + pd.Timedelta(minutes=1) if stop_order else entry_time
    prior_path = bars.loc[(bars.index >= first_path) & (bars.index < exit_time)]
    if trailing:
        prior_path = prior_path.iloc[:0]
    for _, bar in prior_path.iterrows():
        bar_spread_raw = float(bar.spread) * config.point
        bar_spread = (bar_spread_raw if bar_spread_raw > 0
                      else config.fallback_spread_price) * config.spread_multiplier
        if trade.direction == "long":
            assert bar.low > trade.stop and bar.high < trade.take_profit
        else:
            assert bar.high + bar_spread < trade.stop
            assert bar.low + bar_spread > trade.take_profit

    exit_bar = bars.loc[exit_time]
    exit_spread_raw = float(exit_bar.spread) * config.point
    exit_spread = (exit_spread_raw if exit_spread_raw > 0
                   else config.fallback_spread_price) * config.spread_multiplier
    if trade.exit_reason == "stop":
        touched = (exit_bar.low <= trade.stop if trade.direction == "long"
                   else exit_bar.high + exit_spread >= trade.stop)
        assert touched
    elif trade.exit_reason == "target":
        touched = (exit_bar.high >= trade.take_profit if trade.direction == "long"
                   else exit_bar.low + exit_spread <= trade.take_profit)
        assert touched
    elif trade.exit_reason == "time":
        flat = config.flat_time or config.session_exit
        assert exit_time.tz_convert(config.session_timezone).strftime("%H:%M") >= flat
    elif trade.exit_reason in ("trail_stop", "trail_stop_gap"):
        assert trailing
        if trade.exit_reason == "trail_stop":  # filled at a stop tighter than the initial one
            moved = (trade.exit + config.slippage_price if trade.direction == "long"
                     else trade.exit - config.slippage_price)
            assert (moved > trade.stop if trade.direction == "long" else moved < trade.stop)
    elif trade.exit_reason.endswith("_gap"):
        assert (exit_bar.open <= trade.stop if trade.direction == "long" else
                exit_bar.open + exit_spread >= trade.stop) if trade.exit_reason == "stop_gap" else (
                exit_bar.open >= trade.take_profit if trade.direction == "long" else
                exit_bar.open + exit_spread <= trade.take_profit)
    else:
        raise AssertionError(f"unknown exit reason: {trade.exit_reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    args = parser.parse_args()
    config = load_config(args.config)
    bars, _ = load_m1(config)
    atr = prior_daily_atr(bars)
    trades = pd.read_csv(Path(config.output_dir) / "trades.csv")
    selected: list[int] = []

    def add(frame: pd.DataFrame) -> None:
        for index in frame.index:
            if index not in selected:
                selected.append(int(index))
                break

    for direction in ("long", "short"):
        for reason in ("stop", "target", "time"):
            add(trades[(trades.direction == direction) & (trades.exit_reason == reason)])
    for day in ("2024-03-08", "2024-03-11", "2024-11-01", "2024-11-04"):
        add(trades[trades.session_date == day])
    add(trades[trades.ambiguous_m1 == True])  # noqa: E712
    add(trades[trades.exit_reason.str.endswith("_gap")])
    for index in trades.index:
        if len(selected) >= 12:
            break
        if index not in selected:
            selected.append(int(index))
    results = [audit(trades.loc[index], bars, config, atr) for index in selected]
    out = Path(config.output_dir) / "trade_audit.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Audited {len(results)} trades against cached M1 bars; all passed. {out}")


if __name__ == "__main__":
    main()
