"""Hand-built scenarios for the ORB engine.  Run with:  pytest -q"""
import numpy as np
import pandas as pd
import pytest

from orb import Params, run_backtest, resample
from orb.synthetic import make_synthetic_m1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def bars(rows, volume=100.0, spread=0.0):
    """rows: (time, open, high, low, close)."""
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["time"])
    df["tick_volume"] = volume
    df["spread"] = spread
    return df.set_index("time")


def m15_day(day, seq, orb=("16:30", 95, 100, 90, 95)):
    """A day of M15 bars: two quiet bars, the ORB bar, then ``seq`` (each (time, o, h, l, c))."""
    rows = [
        (f"{day} 16:00", 95, 96, 94, 95),
        (f"{day} 16:15", 95, 96, 94, 95),
        (f"{day} {orb[0]}", *orb[1:]),
    ]
    rows += [(f"{day} {t}", o, h, l, c) for (t, o, h, l, c) in seq]
    return rows


def base_params(**kw):
    p = Params(orb_hour=16, orb_minute=30, cutoff_hour=19, tp_ratio=1.5, sl_ratio=1.0, risk_pct=2.0,
               atr_period=0, point=0.01, point_value=1.0, initial_balance=10_000.0)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


# --------------------------------------------------------------------------- #
# close-confirmation entries
# --------------------------------------------------------------------------- #
def test_long_breakout_hits_target():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 99, 93, 96),          # closes inside
        ("17:00", 96, 102, 95, 101),        # closes above 100 -> signal
        ("17:15", 101.5, 120, 101, 118),    # entry at open 101.5, TP hit
        ("17:30", 118, 119, 117, 118),
    ])
    res = run_backtest(bars(rows), base_params())
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["direction"] == "long"
    assert t["entry_time"] == pd.Timestamp("2025-05-06 17:15")
    assert t["entry"] == pytest.approx(101.5)
    assert t["sl"] == pytest.approx(90.0)                    # orb high - 1.0 x range
    assert t["tp"] == pytest.approx(101.5 + 11.5 * 1.5)
    assert t["reason"] == "TP"
    assert t["r"] == pytest.approx(1.5)
    # money: risk 2% of 10 000 = 200 over 11.5 points -> 17.39 lots
    assert t["lots"] == pytest.approx(17.39)
    assert t["profit"] == pytest.approx(17.25 * 17.39)
    assert res.stats["final_balance"] == pytest.approx(10_000 + 17.25 * 17.39)


def test_first_bar_after_orb_can_signal_and_gap_rule():
    # the bar right after the ORB gaps open above the high and closes above: prev close (ORB bar, inside) <= high -> valid
    rows = m15_day("2025-05-06", [
        ("16:45", 103, 106, 102, 105),
        ("17:00", 105, 130, 104, 128),      # entry at 105, TP = 105 + 15*1.5 = 127.5 hit
    ])
    res = run_backtest(bars(rows), base_params())
    assert len(res.trades) == 1
    assert res.trades.iloc[0]["entry"] == pytest.approx(105.0)
    assert res.trades.iloc[0]["reason"] == "TP"


def test_only_first_breakout_of_day_regardless_of_direction():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),        # long signal
        ("17:00", 101, 101, 85, 92),        # entry 101, stopped at 90 (bar low 85 <= 90), closes back inside
        ("17:15", 92, 93, 85, 87),          # later short signal must be ignored
        ("17:30", 87, 88, 60, 65),
    ])
    res = run_backtest(bars(rows), base_params())
    assert len(res.trades) == 1
    assert res.trades.iloc[0]["reason"] == "SL"
    assert res.trades.iloc[0]["r"] == pytest.approx(-1.0)


def test_cutoff_blocks_entry_bar_at_or_after_cutoff_hour():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 99, 93, 96),
        ("17:00", 96, 99, 93, 96),
        ("17:15", 96, 99, 93, 96),
        ("17:30", 96, 99, 93, 96),
        ("17:45", 96, 99, 93, 96),
        ("18:00", 96, 99, 93, 96),
        ("18:15", 96, 99, 93, 96),
        ("18:30", 96, 99, 93, 96),
        ("18:45", 96, 103, 95, 102),        # signal ... but the entry bar would be 19:00
        ("19:00", 102, 130, 101, 128),
    ])
    res = run_backtest(bars(rows), base_params())
    assert res.trades.empty


def test_short_breakout_uses_ask_for_exits():
    p = base_params(spread=1.0)
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 96, 88, 89),          # closes below 90 -> short signal
        ("17:00", 88.5, 99.2, 85, 98),      # entry bid 88.5; SL = 90 + 10 = 100; bar high 99.2 + spread 1.0 = 100.2 >= 100 -> SL
        ("17:15", 98, 99, 97, 98),
    ])
    res = run_backtest(bars(rows), p)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["direction"] == "short"
    assert t["entry"] == pytest.approx(88.5)
    assert t["sl"] == pytest.approx(100.0)
    assert t["reason"] == "SL"
    assert t["exit"] == pytest.approx(100.0)
    assert t["r"] == pytest.approx(-1.0)


def test_same_bar_rule():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 130, 80, 100),       # touches both the stop (90) and the target (117.5)
    ])
    conservative = run_backtest(bars(rows), base_params(same_bar_rule="sl_first"))
    optimistic = run_backtest(bars(rows), base_params(same_bar_rule="tp_first"))
    assert conservative.trades.iloc[0]["reason"] == "SL"
    assert optimistic.trades.iloc[0]["reason"] == "TP"


def test_gap_through_stop_fills_at_open():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 101.5, 100, 100.5),  # entry 101
        ("17:15", 70, 75, 65, 72),          # opens far below the 90 stop
    ])
    res = run_backtest(bars(rows), base_params())
    t = res.trades.iloc[0]
    assert t["reason"] == "SL"
    assert t["exit"] == pytest.approx(70.0)
    assert t["r"] < -1.0


def test_end_of_day_close():
    seq = [("16:45", 95, 102, 94, 101), ("17:00", 101, 103, 100, 102)]
    # flat drift until the close time, nothing hits
    t = pd.Timestamp("2025-05-06 17:15")
    while t < pd.Timestamp("2025-05-06 23:00"):
        seq.append((t.strftime("%H:%M"), 102, 103, 101, 102))
        t += pd.Timedelta(minutes=15)
    res = run_backtest(bars(m15_day("2025-05-06", seq)), base_params(eod_close="22:45"))
    tr = res.trades.iloc[0]
    assert tr["reason"] == "EOD"
    assert tr["exit_time"] == pd.Timestamp("2025-05-06 22:45")
    assert tr["exit"] == pytest.approx(102.0)


def test_two_days_reset_independently():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 130, 100, 125),
    ])
    rows += m15_day("2025-05-07", [
        ("16:45", 95, 96, 85, 87),          # short day
        ("17:00", 86, 87, 60, 65),
    ])
    res = run_backtest(bars(rows), base_params())
    assert list(res.trades["direction"]) == ["long", "short"]
    assert list(res.trades["reason"]) == ["TP", "TP"]
    assert len(res.days) == 2


def test_direction_filter():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 130, 100, 125),
    ])
    assert run_backtest(bars(rows), base_params(direction="short")).trades.empty
    assert len(run_backtest(bars(rows), base_params(direction="long")).trades) == 1


# --------------------------------------------------------------------------- #
# filters and management
# --------------------------------------------------------------------------- #
def test_atr_range_filter_skips_wide_days():
    rows = []
    # 20 ordinary days (range 10) then one day with a huge range (100)
    for k in range(20):
        day = (pd.Timestamp("2025-05-05") + pd.Timedelta(days=k)).strftime("%Y-%m-%d")
        rows += m15_day(day, [("16:45", 95, 102, 94, 101), ("17:00", 101, 130, 100, 125)])
    wide = "2025-05-25"
    rows += m15_day(wide, [("16:45", 95, 152, 94, 151), ("17:00", 151, 330, 150, 325)],
                    orb=("16:30", 100, 150, 50, 100))
    res = run_backtest(bars(rows), base_params(atr_period=14, max_range_atr=2.0))   # ATR ~36, wide range 100 -> 2.8x
    d = res.days.set_index("date")
    assert d.loc[pd.Timestamp(wide).date(), "status"] == "skipped: range too large"
    assert d["status"].value_counts()["traded"] >= 20


def test_volume_spike_filter():
    quiet = bars(m15_day("2025-05-06", [("16:45", 95, 102, 94, 101), ("17:00", 101, 130, 100, 125)]))
    quiet["tick_volume"] = 100.0            # no spike on the range candle
    res = run_backtest(quiet, base_params(vol_lookback=2, vol_spike_min=1.5))
    assert res.trades.empty and res.days.iloc[0]["status"] == "skipped: no volume spike"
    spiky = quiet.copy()
    spiky.loc[pd.Timestamp("2025-05-06 16:30"), "tick_volume"] = 1000.0
    res = run_backtest(spiky, base_params(vol_lookback=2, vol_spike_min=1.5))
    assert len(res.trades) == 1
    assert res.days.iloc[0]["vol_ratio"] == pytest.approx(10.0)


def test_breakeven_then_stopped_at_entry():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 113, 100.5, 112),    # +12 >= 1R (11) -> stop moves to 101 for the next bar
        ("17:15", 112, 113, 100, 101),      # retraces to the entry -> stopped at breakeven
        ("17:30", 101, 102, 100, 101),
    ])
    res = run_backtest(bars(rows), base_params(breakeven_at_r=1.0))
    t = res.trades.iloc[0]
    assert t["reason"] == "SL"
    assert t["exit"] == pytest.approx(101.0)
    assert t["r"] == pytest.approx(0.0)


def test_trailing_stop_locks_profit():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 123, 100.5, 122),    # +22 = 2R -> trail: stop = 123 - 1R(11) = 112
        ("17:15", 122, 124, 111, 111.5),    # dips to 111 <= 112 -> stopped at 112
    ])
    res = run_backtest(bars(rows), base_params(tp_ratio=5.0, trail_start_r=1.0, trail_dist_r=1.0, trail_step_r=0.1))
    t = res.trades.iloc[0]
    assert t["reason"] == "SL"
    assert t["exit"] == pytest.approx(112.0)
    assert t["r"] == pytest.approx(1.0)


def test_trend_filter_vetoes_and_consumes_direction():
    # 60-minute EMA(2) on a falling series -> price below MA -> long vetoed
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 130, 100, 125),
    ])
    df = bars(rows)
    # prepend a long falling history so the HTF MA sits far above price
    hist = []
    t = pd.Timestamp("2025-05-05 00:00")
    px = 500.0
    while t < pd.Timestamp("2025-05-06 16:00"):
        hist.append((t, px, px + 1, px - 1, px))
        t += pd.Timedelta(minutes=15)
        px -= 0.25
    df = pd.concat([bars(hist), df]).sort_index()
    res = run_backtest(df, base_params(filter_ma_period=20, filter_tf_minutes=60))
    assert res.trades.empty
    d = res.days.set_index("date")
    assert d.loc[pd.Timestamp("2025-05-06").date(), "long"] == "filtered"


def test_midpoint_stop_mode():
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 120, 100, 118),
    ])
    result = run_backtest(bars(rows), base_params(stop_mode="midpoint"))
    trade = result.trades.iloc[0]
    assert trade.sl == pytest.approx(95.0)
    assert trade.risk_dist == pytest.approx(6.0)


def test_range_vs_prior_window_filter():
    history = []
    for timestamp in pd.date_range("2025-05-06 14:30", "2025-05-06 15:45", freq="15min"):
        history.append((timestamp, 95, 96, 94, 95))
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 120, 100, 118),
    ])
    result = run_backtest(
        bars(history + rows),
        base_params(range_lookback_minutes=120, max_range_lookback_ratio=0.25),
    )
    assert result.trades.empty
    assert result.days.iloc[0].status == "skipped: range too large vs lookback"
    assert result.days.iloc[0].range_lookback_ratio == pytest.approx(5.0)


def test_range_lookback_filter_rejects_incomplete_history():
    result = run_backtest(
        bars(m15_day("2025-05-06", [("16:45", 95, 102, 94, 101),
                                     ("17:00", 101, 120, 100, 118)])),
        base_params(range_lookback_minutes=120, max_range_lookback_ratio=0.25),
    )
    assert result.trades.empty
    assert result.days.iloc[0].status == "skipped: insufficient range lookback history"


def test_three_same_color_closes_confirm_breakout():
    rows = m15_day("2025-05-06", [
        ("16:45", 99, 101.5, 98, 101),
        ("17:00", 101, 102.5, 100.5, 102),
        ("17:15", 102, 103.5, 101.5, 103),
        ("17:30", 103, 120, 102, 118),
    ])
    result = run_backtest(
        bars(rows),
        base_params(confirmation_bars=3, confirmation_same_color=True),
    )
    assert len(result.trades) == 1
    assert result.trades.iloc[0].signal_time == pd.Timestamp("2025-05-06 17:15")
    assert result.trades.iloc[0].entry_time == pd.Timestamp("2025-05-06 17:30")


def test_confirmation_rejects_breakout_that_reverses_inside():
    rows = m15_day("2025-05-06", [
        ("16:45", 99, 102, 98, 101),
        ("17:00", 101, 102, 94, 96),
        ("17:15", 96, 99, 94, 97),
    ])
    result = run_backtest(bars(rows), base_params(confirmation_bars=2))
    assert result.trades.empty


def forecast_history_rows():
    return (
        m15_day("2025-05-05", [], orb=("16:30", 100, 105, 95, 100))
        + m15_day("2025-05-06", [], orb=("16:30", 100, 110, 90, 100))
        + m15_day("2025-05-07", [], orb=("16:30", 200, 230, 170, 200))
    )


def test_historical_percentile_normalizes_prior_opening_ranges():
    result = run_backtest(
        bars(forecast_history_rows()),
        base_params(range_mode="historical_percentile", range_history_sessions=2,
                    range_percentile=50, range_multiplier=1.0),
    )
    day = result.days.iloc[-1]
    assert day.orb_range == pytest.approx(30.0)  # median of 10% and 20%, applied to today's 200 open
    assert day.orb_low == pytest.approx(185.0)
    assert day.orb_high == pytest.approx(215.0)
    assert result.days.iloc[0].status == "skipped: insufficient session history"


@pytest.mark.parametrize("multiplier, expected_width", [(0.37, 11.1), (2.25, 67.5), (11.8, 354.0)])
def test_historical_range_multiplier_accepts_any_positive_decimal(multiplier, expected_width):
    result = run_backtest(
        bars(forecast_history_rows()),
        base_params(range_mode="historical_percentile", range_history_sessions=2,
                    range_percentile=50, range_multiplier=multiplier),
    )
    assert result.days.iloc[-1].orb_range == pytest.approx(expected_width)


def test_premarket_adjustment_expands_width_when_current_volatility_is_higher():
    rows = []
    for day in ("2025-05-05", "2025-05-06"):
        rows += [(f"{day} 16:00", 100, 101, 99, 101),
                 (f"{day} 16:15", 101, 102, 99, 100),
                 (f"{day} 16:30", 100, 105, 95, 100)]
    rows += [("2025-05-07 16:00", 100, 105, 99, 104),
             ("2025-05-07 16:15", 104, 105, 99, 100),
             ("2025-05-07 16:30", 100, 105, 95, 100)]
    frame = bars(rows)
    plain = run_backtest(frame, base_params(
        range_mode="historical_percentile", range_history_sessions=2,
        range_percentile=50, premarket_window_minutes=30))
    adaptive = run_backtest(frame, base_params(
        range_mode="historical_percentile_premarket", range_history_sessions=2,
        range_percentile=50, premarket_window_minutes=30, premarket_sensitivity=0.5))
    assert plain.days.iloc[-1].orb_range == pytest.approx(10.0)
    assert adaptive.days.iloc[-1].orb_range > plain.days.iloc[-1].orb_range


def test_flat_completed_ema_rejects_breakout_when_slope_filter_is_on():
    history = []
    for timestamp in pd.date_range("2025-05-05 00:00", "2025-05-06 15:45", freq="15min"):
        history.append((timestamp, 95, 96, 94, 95))
    rows = m15_day("2025-05-06", [
        ("16:45", 95, 102, 94, 101),
        ("17:00", 101, 120, 100, 118),
    ])
    result = run_backtest(
        bars(history + rows),
        base_params(filter_ma_period=3, filter_tf_minutes=60,
                    filter_ma_slope_bars=2, filter_ma_min_slope_pct=0.01),
    )
    assert result.trades.empty
    day = result.days.set_index("date").loc[pd.Timestamp("2025-05-06").date()]
    assert day["long"] == "filtered"


# --------------------------------------------------------------------------- #
# immediate mode on M1 path data
# --------------------------------------------------------------------------- #
def test_immediate_entry_on_m1_path():
    m15 = bars(m15_day("2025-05-06", [
        ("16:45", 95, 104, 94, 103),
        ("17:00", 103, 130, 102, 128),
    ]))
    # M1 path for 16:45..17:29: price drifts up through the 100 high inside the 16:47 bar
    m1_rows = []
    t = pd.Timestamp("2025-05-06 16:45")
    px = 95.0
    for k in range(45):
        o = px
        c = px + 1.0
        m1_rows.append((t, o, max(o, c) + 0.2, min(o, c) - 0.2, c))
        px = c
        t += pd.Timedelta(minutes=1)
    m1 = bars(m1_rows)
    res = run_backtest(m15, base_params(entry_mode="immediate"), path=m1)
    assert len(res.trades) == 1
    tr = res.trades.iloc[0]
    assert tr["entry_time"] == pd.Timestamp("2025-05-06 16:49")   # first bar whose high > 100
    assert tr["entry"] == pytest.approx(100.01)                  # range high + one point
    assert tr["reason"] == "TP"


# --------------------------------------------------------------------------- #
# synthetic end-to-end
# --------------------------------------------------------------------------- #
def test_synthetic_run_is_consistent():
    m1 = make_synthetic_m1(days=30, seed=1)
    m15 = resample(m1, 15)
    res = run_backtest(m15, base_params(spread=1.0, atr_period=14), path=m1)
    assert len(res.days) == 30
    assert res.stats["trades"] == len(res.trades)
    # every trade's R agrees with its prices
    t = res.trades
    np.testing.assert_allclose(t["r"], (t["exit"] - t["entry"]) * t["dir"] / t["risk_dist"])
    # winners hit exactly the target, losers exactly the stop (no gaps in this synthetic set)
    assert (t.loc[t["reason"] == "TP", "r"] - 1.5).abs().max() < 1e-9
    assert (t.loc[t["reason"] == "SL", "r"] + 1.0).abs().max() < 0.5
    # balance curve is the running sum of trade profits
    assert res.equity.iloc[-1] == pytest.approx(10_000 + t["profit"].sum())


def test_same_bar_exit_updates_balance_before_later_entry():
    from orb.engine import _simulate_money
    frame = pd.DataFrame([
        {'entry_time': pd.Timestamp('2025-01-01 17:00'), 'exit_time': pd.Timestamp('2025-01-01 17:00'),
         'entry': 100., 'exit': 110., 'dir': 1, 'risk_dist': 10.},
        {'entry_time': pd.Timestamp('2025-01-01 17:15'), 'exit_time': pd.Timestamp('2025-01-01 17:30'),
         'entry': 100., 'exit': 90., 'dir': 1, 'risk_dist': 10.},
    ])
    trades, balance = _simulate_money(frame, Params(initial_balance=1000, risk_pct=10), '2025-01-01')
    assert list(trades.lots) == [10., 11.]
    assert balance.iloc[-1] == 990.


def test_immediate_entry_does_not_fill_stop_at_pre_entry_open():
    rows = m15_day('2025-05-06', [('16:45', 95, 101, 94, 100), ('17:00', 100, 101, 99, 100)])
    result = run_backtest(bars(rows), base_params(entry_mode='immediate', sl_ratio=0.25, direction='long'))
    trade = result.trades.iloc[0]
    assert trade['entry'] == pytest.approx(100.01)
    assert trade['sl'] == pytest.approx(97.5)
    assert trade['exit'] == pytest.approx(97.5)  # cannot exit at 95, before the entry crossing
