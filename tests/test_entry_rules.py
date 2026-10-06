"""Signal-time entry rules, broker-clock decoding and the multi-arm walk-forward wiring."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from orb_research.config import load_config
from orb_research.data_loader import decode_broker_clock
from orb_research.filter_arms import (Arm, comparison_key, entry_window_minutes, prespecified_arms,
                                      run_arms, training_cutoffs)
from orb_research.strategy import (Signal, entry_rules_pass, find_signals, minutes_since_range_end,
                                   range_width_atr)


BASE = load_config("configs/walk_forward_base.json")
NY = ZoneInfo("America/New_York")


def test_config_validates_the_new_keys() -> None:
    replace(BASE, entry_cutoff_minutes_after_or=120.0, min_or_width_atr=0.1).validate()
    with pytest.raises(ValueError, match="entry_cutoff"):
        replace(BASE, entry_cutoff_minutes_after_or=0.0).validate()
    with pytest.raises(ValueError, match="entry_cutoff"):
        replace(BASE, entry_cutoff_minutes_after_or=401.0).validate()  # 16:00 - 08:20 - 60 = 400
    with pytest.raises(ValueError, match="min_or_width_atr"):
        replace(BASE, min_or_width_atr=-0.1).validate()
    with pytest.raises(ValueError, match="broker_clock"):
        replace(BASE, broker_clock="eet").validate()
    assert entry_window_minutes(BASE) == 400.0


def _synthetic_days(plan: list[tuple[float, int]], start_day: str = "2023-01-02") -> pd.DataFrame:
    """One weekday per plan entry: (range width, breakout minutes after range end)."""
    frames = []
    day = pd.Timestamp(start_day).date()
    for width, breakout_after in plan:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        open_local = datetime.combine(day, datetime.strptime("07:00", "%H:%M").time(), NY)
        index = pd.date_range(pd.Timestamp(open_local).tz_convert("UTC"), periods=11 * 60, freq="min")
        range_start = pd.Timestamp(datetime.combine(day, datetime.strptime("08:20", "%H:%M").time(), NY)).tz_convert("UTC")
        range_end = range_start + pd.Timedelta(minutes=60)
        close = pd.Series(100.0, index=index)
        # opening range: oscillate between 100 and 100 + width, then sit at the midpoint
        in_range = (index >= range_start) & (index < range_end)
        close[in_range] = np.where(np.arange(in_range.sum()) % 2 == 0, 100.0, 100.0 + width)
        after = index >= range_end
        close[after] = 100.0 + width / 2
        # breakout: from the chosen minute on, price sits well above the range (clears any buffer)
        breakout_time = range_end + pd.Timedelta(minutes=breakout_after)
        close[index >= breakout_time] = 100.0 + width + 8.0
        frame = pd.DataFrame({"open": close, "high": close + 0.01, "low": close - 0.01, "close": close,
                              "tick_volume": 1, "spread": 20, "real_volume": 0}, index=index)
        frames.append(frame)
        day += timedelta(days=1)
    return pd.concat(frames)


def test_engine_rules_match_post_filtering_the_unfiltered_scan() -> None:
    # 16 warm-up days so ATR14 exists, then 12 days with controlled widths and breakout timing.
    plan = [(2.0, 60)] * 16 + [(0.5, 30), (2.0, 30), (3.0, 90), (0.5, 240), (2.0, 240), (3.0, 300),
                                (0.5, 60), (2.0, 120), (3.0, 60), (0.5, 180), (2.0, 360), (3.0, 30)]
    m1 = _synthetic_days(plan)
    base = replace(BASE, breakout_buffer_atr=0.25)
    unfiltered, counts = find_signals(m1, base)
    assert counts["late_breakout"] == 0 and counts["narrow_range"] == 0
    assert len(unfiltered) >= 12
    timing = [minutes_since_range_end(signal) for signal in unfiltered[-12:]]
    assert timing == [35, 35, 95, 245, 245, 305, 65, 125, 65, 185, 365, 35]  # M5 close after breakout
    widths = np.array([range_width_atr(signal) for signal in unfiltered[-12:]])
    assert widths[0] < widths[1] < widths[2]

    cutoff, floor = 120.0, float(np.percentile(widths, 40))
    ruled, ruled_counts = find_signals(m1, replace(base, entry_cutoff_minutes_after_or=cutoff,
                                                   min_or_width_atr=floor))
    expected = [signal for signal in unfiltered if entry_rules_pass(signal, cutoff, floor)]
    assert [s.session_date for s in ruled] == [s.session_date for s in expected]
    assert len(ruled) < len(unfiltered)
    assert ruled_counts["narrow_range"] == sum(range_width_atr(s) < floor for s in unfiltered)
    assert ruled_counts["late_breakout"] == sum(range_width_atr(s) >= floor and
                                                minutes_since_range_end(s) > cutoff for s in unfiltered)
    # the cutoff is inclusive, the floor is inclusive
    assert entry_rules_pass(unfiltered[-1], 35.0, None) and not entry_rules_pass(unfiltered[-1], 34.9, None)
    exact = range_width_atr(unfiltered[-1])
    assert entry_rules_pass(unfiltered[-1], None, exact) and not entry_rules_pass(unfiltered[-1], None, exact + 1e-9)


def test_broker_clock_decoding_moves_0830_releases_to_new_york_0830() -> None:
    # winter CPI, a CPI inside the US-summer / EU-winter mismatch week, summer CPI
    stored = pd.DatetimeIndex(["2024-02-13 15:30", "2024-03-12 15:30", "2024-06-12 15:30"], tz="UTC")
    frame = pd.DataFrame({"close": [1.0, 2.0, 3.0]}, index=stored)
    assert decode_broker_clock(frame, BASE).index.equals(stored)  # default keeps the stored labels
    decoded = decode_broker_clock(frame, replace(BASE, broker_clock="new_york_plus_7"))
    assert [stamp.strftime("%H:%M") for stamp in decoded.index.tz_convert("America/New_York")] == ["08:30"] * 3
    assert [stamp.strftime("%H:%M") for stamp in decoded.index] == ["13:30", "12:30", "12:30"]
    assert decoded.close.to_list() == [1.0, 2.0, 3.0]


def _signal(date: str, minutes_after: int, width: float, atr: float = 10.0) -> Signal:
    start = pd.Timestamp(f"{date} 13:20", tz="UTC")
    end = start + pd.Timedelta(minutes=60)
    close = end + pd.Timedelta(minutes=minutes_after)
    return Signal(date, "new_york_0820", 1, start, end, 100.0 + width, 100.0,
                  close - pd.Timedelta(minutes=5), close, 100.0 + width + 1, atr)


def test_training_cutoffs_use_only_completed_training_trades_inside_the_window() -> None:
    signals = {f"2020-01-{day:02d}": _signal(f"2020-01-{day:02d}", 10 * day, day / 10) for day in range(1, 11)}
    trades = pd.DataFrame({"session_date": list(signals)[:8]})  # two signals never became trades
    cutoff, floor = training_cutoffs(signals, trades, 400.0, Arm("x", early=True, width=True))
    assert cutoff == pytest.approx(np.percentile([10 * d for d in range(1, 9)], 60))
    assert floor == pytest.approx(np.percentile([d / 100 for d in range(1, 9)], 20))
    # a trade whose first breakout fell outside the base window is not eligible
    late = dict(signals)
    late["2020-01-08"] = _signal("2020-01-08", 500, 0.8)
    cutoff_late, _ = training_cutoffs(late, trades, 400.0, Arm("x", early=True))
    assert cutoff_late == pytest.approx(np.percentile([10 * d for d in range(1, 8)], 60))
    assert training_cutoffs(signals, trades, 400.0, Arm("x")) == (None, None)


def test_arm_plan_and_comparisons() -> None:
    arms = prespecified_arms()
    keys = [arm.key for arm in arms]
    assert keys[0] == "base_1600_fixed" and len(keys) == len(set(keys)) == 15
    assert comparison_key(arms[0]) is None
    assert comparison_key(Arm("early_1600_fixed", early=True)) == "base_1600_fixed"
    assert comparison_key(Arm("both_1830_fixed", True, True, "18:30")) == "base_1830_fixed"
    assert comparison_key(Arm("base_1700_fixed", session_exit="17:00")) == "base_1600_fixed"
    assert comparison_key(Arm("both_1600_joint", True, True, None, "joint")) == "base_1600_fixed"


def test_run_arms_freezes_rules_from_training_and_keeps_the_entry_window(monkeypatch) -> None:
    dates = pd.DatetimeIndex([f"{year}-06-01" for year in range(2018, 2025)], tz="UTC")
    m1 = pd.DataFrame({"close": [1.0] * len(dates)}, index=dates)
    # Every year has 10 sessions; breakout timing 10..100 min, widths 0.1..1.0 ATR; late sessions lose.
    universe: dict[str, Signal] = {}
    for year in range(2018, 2025):
        for k in range(1, 11):
            date = f"{year}-03-{k:02d}"
            universe[date] = _signal(date, 10 * k + (60 if k == 10 else 0), k / 10)
    scans: list[tuple[int, float, str, float | None, float | None]] = []

    def fake_find_signals(prefix, config):
        latest = prefix.index[-1].year
        scans.append((latest, config.breakout_buffer_atr, config.session_exit,
                      config.entry_cutoff_minutes_after_or, config.min_or_width_atr))
        out = [s for date, s in sorted(universe.items()) if int(date[:4]) <= latest
               and entry_rules_pass(s, config.entry_cutoff_minutes_after_or, config.min_or_width_atr)]
        return out, {"incomplete_range": 0, "late_breakout": 0, "narrow_range": 0}

    def fake_simulate(prefix, signals, config, initial_equity=None):
        equity = config.initial_equity if initial_equity is None else initial_equity
        rows = []
        for s in signals:
            k = int(s.session_date[-2:])
            score = -1.0 if k >= 7 else 0.5  # late / wide sessions lose, early ones win
            if config.breakout_buffer_atr == 0.35:
                # 0.35 is worse unfiltered (its late sessions lose more) but best once the
                # early rule removes them, so only joint selection should pick it.
                score += -0.5 if k >= 7 else 0.1
            if config.session_exit == "18:30":
                score += 0.2
            pnl = equity * config.risk_percent / 100 * score
            equity += pnl
            rows.append({"R_result": score, "PnL": pnl, "equity_after": equity, "exit_reason": "time",
                         "session_date": s.session_date, "signal_close": s.signal_close,
                         "range_end": s.range_end})
        return pd.DataFrame(rows), {"signals": len(signals), "trades": len(rows), "skipped": {}}

    monkeypatch.setattr("orb_research.filter_arms.find_signals", fake_find_signals)
    monkeypatch.setattr("orb_research.filter_arms.simulate_signals", fake_simulate)
    arms = (Arm("base_1600_fixed"), Arm("early_1600_fixed", early=True),
            Arm("both_1830_fixed", True, True, "18:30"), Arm("base_1700_fixed", session_exit="17:00"),
            Arm("early_1600_joint", early=True, selection="joint"))
    results = run_arms(m1, BASE, arms, emit=lambda _: None)
    early = results["arms"]["early_1600_fixed"]["folds"]
    # P60 of {10,...,90,160} over the training trades = 64 -> keeps k <= 6 in the test year
    assert early.cutoff_minutes.to_list() == pytest.approx([64.0] * 4)
    assert early.trades.to_list() == [6, 6, 6, 6]
    assert early.min_width_atr.isna().all()
    both = results["arms"]["both_1830_fixed"]["folds"]
    assert both.cutoff_minutes.to_list() == pytest.approx([64.0] * 4)
    assert both.min_width_atr.to_list() == pytest.approx([0.028] * 4)  # P20 of 0.01..0.10
    assert both.trades.to_list() == [4, 4, 4, 4]  # timing keeps k <= 6, the width floor drops k <= 2
    exit_only = results["arms"]["base_1700_fixed"]["folds"]
    assert exit_only.cutoff_minutes.to_list() == [400.0] * 4  # base entry window kept
    assert exit_only.trades.to_list() == [10, 10, 10, 10]
    joint = results["arms"]["early_1600_joint"]["folds"]
    assert joint.selected_buffer.to_list() == [0.35] * 4
    assert results["arms"]["base_1600_fixed"]["folds"].selected_buffer.to_list() == [0.25] * 4
    # test-year scans ran through the engine path with the frozen rules set on the config
    assert any(latest == 2021 and cutoff == pytest.approx(64.0) for latest, _, _, cutoff, _ in scans)
    paired = results["paired"].set_index("arm")
    assert paired.loc["early_1600_fixed", "removed_sessions"] == 16
    assert paired.loc["early_1600_fixed", "expectancy_difference"] > 0
    assert paired.loc["base_1700_fixed", "common_sessions"] == 40


def test_eastern_european_broker_clock_follows_eu_daylight_saving() -> None:
    """MOTCapital: 16:30 server is 09:30 New York, except while only the US is on summer time."""
    config = replace(BASE, broker_clock="eastern_european")
    stored = pd.DatetimeIndex([pd.Timestamp("2024-03-05 16:30", tz="UTC"),   # both on winter time
                               pd.Timestamp("2024-03-15 15:30", tz="UTC"),   # US summer, EU winter
                               pd.Timestamp("2024-04-05 16:30", tz="UTC")])  # both on summer time
    frame = pd.DataFrame({"open": 1.0}, index=stored)
    decoded = decode_broker_clock(frame, config).index.tz_convert("America/New_York")
    assert [stamp.strftime("%H:%M") for stamp in decoded] == ["09:30", "09:30", "09:30"]
