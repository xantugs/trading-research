#!/usr/bin/env python3
"""Check representative baseline trades directly against cached FTMO minute bars."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import load_m1


def audit(trade: pd.Series, bars: pd.DataFrame, config) -> dict:
    start = pd.Timestamp(trade.range_start)
    end = pd.Timestamp(trade.range_end)
    signal_open = pd.Timestamp(trade.signal_open)
    entry_time = pd.Timestamp(trade.timestamp)
    exit_time = pd.Timestamp(trade.exit_time)
    opening = bars.loc[(bars.index >= start) & (bars.index < end)]
    assert len(opening) == int((end - start).total_seconds() / 60)
    assert trade.range_high == opening.high.max()
    assert trade.range_low == opening.low.min()

    previous = bars.loc[(bars.index >= end) & (bars.index < signal_open)]
    for _, group in previous.resample(f"{config.signal_minutes}min", label="left", closed="left"):
        if len(group) == config.signal_minutes:
            assert trade.range_low <= group.close.iloc[-1] <= trade.range_high
    signal = bars.loc[(bars.index >= signal_open) & (bars.index < entry_time)]
    assert len(signal) == config.signal_minutes
    assert trade.signal_price == signal.close.iloc[-1]
    assert ((trade.signal_price > trade.range_high) if trade.direction == "long"
            else (trade.signal_price < trade.range_low))

    first = bars.loc[entry_time]
    recorded = float(first.spread) * config.point
    spread = (recorded if recorded > 0 else config.fallback_spread_price) * config.spread_multiplier
    expected_entry = (float(first.open) + spread + config.slippage_price if trade.direction == "long"
                      else float(first.open) - config.slippage_price)
    assert abs(trade.entry - expected_entry) < 1e-7
    if config.stop_method == "opposite_range":
        assert trade.stop == (trade.range_low if trade.direction == "long" else trade.range_high)
    elif config.stop_method == "range_multiple":
        width = trade.range_high - trade.range_low
        expected_stop = (trade.range_high - width * config.stop_width_multiple
                         if trade.direction == "long" else
                         trade.range_low + width * config.stop_width_multiple)
        assert abs(trade.stop - expected_stop) < 1e-7
    expected_target = trade.entry + (trade.entry - trade.stop) * config.reward_risk
    assert abs(trade.take_profit - expected_target) < 1e-7
    assert trade.exit_time >= trade.timestamp
    assert exit_time in bars.index

    prior_path = bars.loc[(bars.index >= entry_time) & (bars.index < exit_time)]
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
        assert exit_time.tz_convert(config.session_timezone).strftime("%H:%M") == config.session_exit
    elif trade.exit_reason.endswith("_gap"):
        assert (exit_bar.open <= trade.stop if trade.direction == "long" else
                exit_bar.open + exit_spread >= trade.stop) if trade.exit_reason == "stop_gap" else (
                exit_bar.open >= trade.take_profit if trade.direction == "long" else
                exit_bar.open + exit_spread <= trade.take_profit)
    else:
        raise AssertionError(f"unknown exit reason: {trade.exit_reason}")
    return {"date": trade.session_date, "direction": trade.direction,
            "signal_utc": str(entry_time), "exit_utc": str(exit_time),
            "exit_reason": trade.exit_reason, "net_R": round(float(trade.R_result), 4),
            "status": "passed"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    args = parser.parse_args()
    config = load_config(args.config)
    bars, _ = load_m1(config)
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
    results = [audit(trades.loc[index], bars, config) for index in selected]
    out = Path(config.output_dir) / "trade_audit.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Audited {len(results)} trades against cached M1 bars; all passed. {out}")


if __name__ == "__main__":
    main()
