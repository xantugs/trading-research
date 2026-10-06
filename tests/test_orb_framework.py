"""ORB-framework EA mechanics: split range/entry timeframes, per-direction entries, held positions."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from orb_research.backtester import backtest, simulate_signals
from orb_research.config import load_config
from orb_research.orb_framework_cli import comparison_markdown, parse_pair, summarise
from orb_research.risk import size_position
from orb_research.strategy import find_signals
from webapp.server import validate_parameters


NY = "America/New_York"
# Costs off so every price in these tests is exact.
BASE = replace(load_config("configs/orb_framework.json"), fallback_spread_price=0.0,
               slippage_price=0.0, commission_rate_pct_side=0.0)


def _local(day: str, hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(datetime.fromisoformat(f"{day}T{hhmm}")).tz_localize(NY).tz_convert("UTC")


def _bars(segments: list[tuple[str, str, float]], days: int = 1, start_day: str = "2023-01-03"):
    """Flat M1 bars at 100, then each (from-local, to-local, price) segment painted over in order.

    Local times are 'YYYY-MM-DD HH:MM' New York. The 09:30-09:45 range alternates 99/101
    every day unless a segment overrides it.
    """
    first = pd.Timestamp(start_day, tz="UTC")
    index = pd.date_range(first, first + pd.Timedelta(days=days + 1), freq="min", inclusive="left")
    price = pd.Series(100.0, index=index)
    for offset in range(days + 1):
        day = (pd.Timestamp(start_day) + pd.Timedelta(days=offset)).date().isoformat()
        in_range = (index >= _local(day, "09:30")) & (index < _local(day, "09:45"))
        price[in_range] = [99.0 if i % 2 == 0 else 101.0 for i in range(in_range.sum())]
    for left, right, value in segments:
        start = _local(*left.split(" "))
        end = _local(*right.split(" "))
        price[(index >= start) & (index < end)] = value
    return pd.DataFrame({"open": price, "high": price, "low": price, "close": price,
                         "tick_volume": 1, "spread": 0, "real_volume": 0}, index=index)


def test_new_config_values_validate() -> None:
    BASE.validate()
    for minutes in (1, 5, 15):
        replace(BASE, signal_minutes=minutes).validate()
    for minutes in (5, 15, 30, 45, 60):
        replace(BASE, range_minutes=minutes).validate()
    for bad in ({"signal_minutes": 3}, {"range_minutes": 10}, {"trade_direction": "up"},
                {"position_exit": "eod"}, {"trades_per_session": "two"}):
        with pytest.raises(ValueError):
            replace(BASE, **bad).validate()


def test_lower_entry_timeframe_confirms_the_same_breakout_earlier() -> None:
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 12:00", 102.0)])
    five, _ = find_signals(m1, replace(BASE, signal_minutes=5))
    fifteen, _ = find_signals(m1, replace(BASE, signal_minutes=15))
    one, _ = find_signals(m1, replace(BASE, signal_minutes=1))
    assert [s.signal_close for s in one] == [_local("2023-01-03", "09:48")]
    assert [s.signal_close for s in five] == [_local("2023-01-03", "09:50")]
    assert [s.signal_close for s in fifteen] == [_local("2023-01-03", "10:00")]
    assert all(s.range_high == 101.0 and s.range_low == 99.0 for s in five + fifteen + one)


def test_a_close_back_inside_the_range_is_not_a_breakout() -> None:
    # 09:45-09:50 pokes above but closes inside; 09:50-09:55 closes outside.
    m1 = _bars([("2023-01-03 09:46", "2023-01-03 09:49", 102.0),
                ("2023-01-03 09:52", "2023-01-03 12:00", 102.0)])
    signals, _ = find_signals(m1, BASE)
    assert [s.signal_close for s in signals] == [_local("2023-01-03", "09:55")]


def test_first_breakout_per_direction() -> None:
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 10:30", 102.0),
                ("2023-01-03 10:30", "2023-01-03 12:00", 98.0)])
    both, _ = find_signals(m1, BASE)
    assert [(s.direction, s.signal_close) for s in both] == [
        (1, _local("2023-01-03", "09:50")), (-1, _local("2023-01-03", "10:35"))]
    one, _ = find_signals(m1, replace(BASE, trades_per_session="one"))
    assert [s.direction for s in one] == [1]


def test_direction_filter_ignores_the_other_side() -> None:
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 10:30", 98.0),
                ("2023-01-03 10:30", "2023-01-03 12:00", 102.0)])
    longs, _ = find_signals(m1, replace(BASE, trade_direction="long", trades_per_session="one"))
    assert [(s.direction, s.signal_close) for s in longs] == [(1, _local("2023-01-03", "10:35"))]
    first, _ = find_signals(m1, replace(BASE, trades_per_session="one"))
    assert [s.direction for s in first] == [-1]


def test_cutoff_hour_blocks_a_bar_closing_at_the_cutoff() -> None:
    m1 = _bars([("2023-01-03 11:56", "2023-01-03 13:00", 102.0)])
    held, _ = find_signals(m1, BASE)
    assert held == []
    flat, _ = find_signals(m1, replace(BASE, position_exit="session_exit"))
    assert [s.signal_close for s in flat] == [_local("2023-01-03", "12:00")]


def test_held_position_runs_overnight_to_its_target() -> None:
    # Long from 102 with the stop at the range low 99: target = 102 + 1.5 × 3 = 106.5.
    m1 = _bars([("2023-01-03 09:47", "2023-01-04 09:30", 102.0),
                ("2023-01-04 09:30", "2023-01-04 09:45", 102.5),
                ("2023-01-04 09:45", "2023-01-04 10:00", 102.0),
                ("2023-01-04 10:00", "2023-01-05 16:00", 107.0)], days=2)
    m1.loc[(m1.index >= _local("2023-01-04", "09:30")) & (m1.index < _local("2023-01-04", "09:45")),
           ["high", "low"]] = [[103.0, 102.0]]
    config = replace(BASE, trades_per_session="one")
    trades, diagnostics = backtest(m1, config)
    first = trades.iloc[0]
    assert (first.entry, first.stop, first.take_profit) == (102.0, 99.0, 106.5)
    assert first.exit_reason.startswith("target")
    assert first.exit_time == _local("2023-01-04", "10:00")
    assert first.R_result > 1.4
    flat, _ = backtest(m1, replace(config, position_exit="session_exit"))
    assert flat.iloc[0].exit_reason == "time"
    assert flat.iloc[0].exit_time == _local("2023-01-03", "12:00")


def test_overlapping_positions_are_sized_from_equity_including_open_profit() -> None:
    # Day 1 long at 102 (stop 99, target 106.5) is still open when day 2 breaks out of a
    # 103-105 range at 106; day 2's size uses the balance plus day 1's open profit of 4/unit.
    m1 = _bars([("2023-01-03 09:47", "2023-01-04 09:30", 102.0),
                ("2023-01-04 09:30", "2023-01-04 09:45", 104.0),
                ("2023-01-04 09:47", "2023-01-05 09:00", 106.0),
                ("2023-01-05 09:00", "2023-01-06 16:00", 111.0)], days=3)
    window = (m1.index >= _local("2023-01-04", "09:30")) & (m1.index < _local("2023-01-04", "09:45"))
    m1.loc[window, ["high", "low"]] = [[105.0, 103.0]]
    config = replace(BASE, trades_per_session="one")
    signals, _ = find_signals(m1, config)
    day_one, day_two = signals[0], signals[1]
    assert (day_one.session_date, day_two.session_date) == ("2023-01-03", "2023-01-04")
    trades, _ = simulate_signals(m1, signals[:2], config)
    first, second = trades.iloc[0], trades.iloc[1]
    assert first.exit_time > second.timestamp  # genuinely overlapping
    open_profit = (106.0 - first.entry) * first.position_size * config.contract_size
    expected_lots, _ = size_position(config.initial_equity + open_profit, second.entry, second.stop, config)
    assert second.position_size == expected_lots
    assert expected_lots > size_position(config.initial_equity, second.entry, second.stop, config)[0]
    # equity_after books trades in exit order
    booked = trades.sort_values("exit_time")
    assert booked.equity_after.iloc[-1] == pytest.approx(config.initial_equity + trades.PnL.sum())


def test_position_open_when_the_data_ends_is_skipped() -> None:
    m1 = _bars([("2023-01-03 09:47", "2023-01-05 00:00", 102.0)])
    trades, diagnostics = backtest(m1, replace(BASE, trades_per_session="one"))
    assert trades.empty
    assert diagnostics["skipped"] == {"open_at_data_end": 1}


def test_comparison_helpers() -> None:
    assert parse_pair("15/5") == (15, 5)
    with pytest.raises(Exception):
        parse_pair("15/3")
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 10:00", 102.0),
                ("2023-01-03 10:00", "2023-01-04 00:00", 107.0)])
    trades, diagnostics = backtest(m1, replace(BASE, trades_per_session="one"))
    row = summarise(trades, diagnostics, replace(BASE, trades_per_session="one"))
    assert row["trades"] == 1 and row["win_rate"] == 1.0 and row["final_balance"] > BASE.initial_equity
    assert "| 15/5 | 15m | 5m | 1 |" in comparison_markdown([row], BASE)


def test_dashboard_accepts_the_new_inputs() -> None:
    base = load_config("configs/xauusd_orb_research.json")
    chosen = validate_parameters(base, {"range_minutes": 15, "signal_minutes": 1,
                                        "trade_direction": "long", "position_exit": "stop_or_target",
                                        "trades_per_session": "one_per_direction"})
    assert chosen["signal_minutes"] == 1 and chosen["position_exit"] == "stop_or_target"
    with pytest.raises(ValueError):
        validate_parameters(base, {"signal_minutes": 3})
    with pytest.raises(ValueError):
        validate_parameters(base, {"signal_minutes": "5"})


# --- Trading Steady channel variants -------------------------------------------------------

def test_variant_config_validation() -> None:
    replace(BASE, entry_mode="stop_order").validate()
    replace(BASE, position_exit="end_of_day", flat_time="16:00").validate()
    for bad in ({"entry_mode": "limit"}, {"trades_per_session": "after_close", "entry_mode": "stop_order"},
                {"position_exit": "end_of_day"}, {"position_exit": "friday_close", "flat_time": "11:00"},
                {"flat_time": "16:00"}, {"or_min_atr": 3.0, "or_max_atr": 1.25}, {"or_candle": "green"},
                {"trail_atr_multiple": 0.0}, {"financing_pct_annual": -1.0}, {"or_atr_period": 0}):
        with pytest.raises(ValueError):
            replace(BASE, **bad).validate()


def _stop_order_day():
    m1 = _bars([("2023-01-03 09:52", "2023-01-03 10:30", 101.2),
                ("2023-01-03 10:30", "2023-01-04 00:00", 104.5)])
    bar = _local("2023-01-03", "09:52")
    m1.loc[bar, ["open", "high", "low", "close"]] = [100.5, 101.5, 100.5, 101.2]
    return m1, bar


def test_stop_order_fills_at_the_range_edge_on_first_touch() -> None:
    m1, bar = _stop_order_day()
    config = replace(BASE, entry_mode="stop_order", trades_per_session="one")
    signals, _ = find_signals(m1, config)
    assert [(s.direction, s.signal_close, s.stop_entry) for s in signals] == [(1, bar, 101.0)]
    trades, _ = backtest(m1, config)
    trade = trades.iloc[0]
    assert (trade.entry, trade.stop, trade.take_profit) == (101.0, 99.0, 104.0)
    assert trade.exit_reason.startswith("target")


def test_stop_order_entry_minute_can_only_stop_out() -> None:
    m1, bar = _stop_order_day()
    m1.loc[bar, ["low"]] = 98.9  # the fill minute also trades through the stop
    trades, _ = backtest(m1, replace(BASE, entry_mode="stop_order", trades_per_session="one"))
    trade = trades.iloc[0]
    assert (trade.exit_reason, trade.exit_time, trade.ambiguous_m1) == ("stop", bar, True)


def test_intraday_atr_range_filter() -> None:
    # Flat at 100 before the range, so ATR(2) on 15-minute bars at the range candle is
    # 0.5 × 0 + 0.5 × 2 = 1.0 against a range width of 2.
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 12:00", 102.0)])
    kept = replace(BASE, or_atr_period=2, or_min_atr=1.5, or_max_atr=2.5)
    assert len(find_signals(m1, kept)[0]) == 1
    for bounds in ({"or_max_atr": 1.5}, {"or_min_atr": 2.5}):
        signals, counts = find_signals(m1, replace(BASE, or_atr_period=2, **bounds))
        assert signals == [] and counts["range_atr_filtered"] == 2  # both sessions in the frame


def test_range_candle_direction_filter() -> None:
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 12:00", 102.0)])  # range opens 99, closes 99
    assert find_signals(m1, replace(BASE, or_candle="bullish"))[0] == []
    assert len(find_signals(m1, replace(BASE, or_candle="bearish"))[0]) == 1
    m1.loc[_local("2023-01-03", "09:44"), ["open", "high", "low", "close"]] = 100.5
    assert len(find_signals(m1, replace(BASE, or_candle="bullish"))[0]) == 1


def test_atr_trailing_stop_replaces_the_target() -> None:
    # Long 102, stop 99, activation at 1.5R = 106.5. At 10:00 price jumps to 107: the 10:00-10:05
    # bar has TR 5, so its close trails the stop to 107 - 5 = 102; the next bar (TR 0) trails it
    # to 107, where the 10:10 minute opens (an open at the stop fills as a gap, as elsewhere).
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 10:00", 102.0),
                ("2023-01-03 10:00", "2023-01-04 00:00", 107.0)])
    config = replace(BASE, trades_per_session="one", trail_atr_multiple=1.0, trail_atr_period=1)
    trade = backtest(m1, config)[0].iloc[0]
    assert (trade.exit_reason, trade.exit, trade.exit_time) == (
        "trail_stop_gap", 107.0, _local("2023-01-03", "10:10"))
    fixed = backtest(m1, replace(config, trail_atr_multiple=None))[0].iloc[0]
    assert fixed.exit_reason.startswith("target") and fixed.exit == 106.5


def test_end_of_day_and_friday_close_exits() -> None:
    m1 = _bars([("2023-01-03 09:47", "2023-01-07 00:00", 102.0)], days=4)
    config = replace(BASE, trades_per_session="one", flat_time="16:00")
    eod = backtest(m1, replace(config, position_exit="end_of_day"))[0]
    assert eod.iloc[0].exit_reason == "time" and eod.iloc[0].exit_time == _local("2023-01-03", "16:00")
    friday = backtest(m1, replace(config, position_exit="friday_close"))[0]
    assert friday.iloc[0].exit_time == _local("2023-01-06", "16:00")  # Tuesday's trade, Friday flat


def test_overnight_financing() -> None:
    from orb_research.backtester import financing_nights
    assert financing_nights(_local("2023-01-06", "10:00"), _local("2023-01-09", "10:00")) == 3
    assert financing_nights(_local("2023-01-03", "10:00"), _local("2023-01-03", "16:59")) == 0
    m1 = _bars([("2023-01-03 09:47", "2023-01-05 10:00", 102.0),
                ("2023-01-05 10:00", "2023-01-06 00:00", 107.0)], days=3)
    config = replace(BASE, trades_per_session="one", financing_pct_annual=7.0)
    trade = backtest(m1, config)[0].iloc[0]
    expected = trade.entry * trade.position_size * config.contract_size * 0.07 / 365 * 2
    assert trade.financing == pytest.approx(expected)
    free = backtest(m1, replace(config, financing_pct_annual=0.0))[0].iloc[0]
    assert free.PnL - trade.PnL == pytest.approx(expected)


def test_after_close_retakes_a_fresh_breakout_once_flat() -> None:
    # Long at 09:50 stops out at 10:00 (98.5 < 99); price returns inside, then a fresh 5m close
    # above 101 at 10:45 is a second long. 10:50 also closes above, but is not fresh.
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 10:00", 102.0),
                ("2023-01-03 10:00", "2023-01-03 10:20", 98.5),
                ("2023-01-03 10:40", "2023-01-04 00:00", 102.0)])
    config = replace(BASE, trade_direction="long", trades_per_session="after_close")
    trades, diagnostics = backtest(m1, config)
    assert list(trades.timestamp) == [_local("2023-01-03", "09:50"), _local("2023-01-03", "10:45")]
    assert trades.iloc[0].exit_reason.startswith("stop")
    assert len(backtest(m1, replace(config, trades_per_session="one"))[0]) == 1


def test_after_close_skips_a_breakout_while_the_session_position_is_open() -> None:
    # Long at 09:50 is still open (stop 99, target 106.5) when price dips inside and breaks out
    # again at 10:15.
    m1 = _bars([("2023-01-03 09:47", "2023-01-03 10:00", 102.0),
                ("2023-01-03 10:00", "2023-01-03 10:10", 100.0),
                ("2023-01-03 10:10", "2023-01-03 11:00", 102.0),
                ("2023-01-03 11:00", "2023-01-04 00:00", 107.0)])
    config = replace(BASE, trade_direction="long", trades_per_session="after_close")
    trades, diagnostics = backtest(m1, config)
    assert len(trades) == 1
    assert diagnostics["skipped"] == {"session_position_open": 1}


def test_dashboard_accepts_channel_variants_and_blank_means_off() -> None:
    base = load_config("configs/xauusd_orb_research.json")
    chosen = validate_parameters(base, {
        "entry_mode": "stop_order", "or_candle": "bullish", "or_atr_period": 1344,
        "or_min_atr": 1.25, "or_max_atr": 3.0, "position_exit": "friday_close", "flat_time": "16:00",
        "trail_atr_multiple": 1.0, "trail_activation_r": 1.5, "trail_atr_period": 14,
        "financing_pct_annual": 7.0})
    assert chosen["or_max_atr"] == 3.0 and chosen["flat_time"] == "16:00"
    off = validate_parameters(base, {"or_min_atr": None, "or_max_atr": None,
                                     "trail_atr_multiple": None, "flat_time": None})
    assert off["trail_atr_multiple"] is None and off["or_min_atr"] is None
    for bad in ({"or_atr_period": 0}, {"or_atr_period": 14.5}, {"trail_atr_multiple": 50.0},
                {"flat_time": "4pm"}, {"position_exit": "end_of_day", "flat_time": None},
                {"entry_mode": "stop_order", "trades_per_session": "after_close"},
                {"risk_percent": None}):
        with pytest.raises(ValueError):
            validate_parameters(base, bad)


def test_holdout_opens_only_with_a_dated_record() -> None:
    from orb_research.config import holdout_note
    sealed = replace(BASE, development_end="2025-01-01", holdout_opened=None)
    sealed.validate()
    assert holdout_note(sealed) == "2025+ final holdout touched: NO"
    with pytest.raises(ValueError, match="holdout_opened"):
        replace(sealed, development_end="2026-09-17").validate()
    opened = replace(sealed, development_end="2026-09-17", holdout_opened="2026-10-06")
    opened.validate()
    assert "not out-of-sample" in holdout_note(opened) and "2026-10-06" in holdout_note(opened)
    assert BASE.holdout_opened == "2026-10-06" and BASE.development_end == "2026-09-17"
