from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from orb import NbboExecution, Params, QuoteDay, TickCoverage, run_backtest


def bars_for_tick_trade(*, ambiguous=False):
    index = pd.date_range("2020-01-02 16:30", "2020-01-02 23:00", freq="15min")
    frame = pd.DataFrame({
        "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.0,
        "tick_volume": 10.0, "spread": 1.0,
    }, index=index)
    frame.loc[index[0], ["open", "high", "low", "close"]] = [95.0, 100.0, 90.0, 95.0]
    frame.loc[index[1], ["open", "high", "low", "close"]] = [95.0, 102.0, 94.0, 101.0]
    if ambiguous:
        frame.loc[index[2], ["open", "high", "low", "close"]] = [101.0, 130.0, 80.0, 100.0]
    return frame


def quote_day(rows):
    times = np.asarray([pd.Timestamp(row[0]).to_datetime64() for row in rows], dtype="datetime64[ns]")
    bids = np.asarray([row[1] for row in rows], dtype=float)
    asks = np.asarray([row[2] for row in rows], dtype=float)
    for values in (times, bids, asks):
        values.setflags(write=False)
    coverage = TickCoverage(
        source_date=date(2020, 1, 2), source_path=Path("QQQ_2020-01-02_quotes.parquet"),
        source_rows=len(rows), rows=len(rows), dropped_timestamps=0, dropped_invalid=0,
        dropped_crossed=0, duplicate_timestamps=0, first=pd.Timestamp(times[0]),
        last=pd.Timestamp(times[-1]), strategy_offset_hours=7,
    )
    return QuoteDay(times, bids, asks, coverage)


class Store:
    strategy_offset_hours = 7

    def __init__(self, quotes=None, error=None):
        self.quotes = quotes
        self.error = error
        self.loaded = []

    def load_day(self, source_date):
        self.loaded.append(source_date)
        if self.error:
            raise self.error
        return self.quotes


def params(**changes):
    values = dict(
        atr_period=0, vol_lookback=0, risk_pct=1.0, point=0.01, point_value=1.0,
        lot_step=1.0, min_lot=1.0, max_lot=100_000.0, initial_balance=100_000.0,
        max_gross_leverage=1.0, commission_per_lot=0.005, eod_close="22:45",
    )
    values.update(changes)
    return Params(**values)


def test_engine_keeps_bar_signals_but_uses_actual_nbbo_entry_and_exit():
    frame = bars_for_tick_trade()
    quotes = quote_day([
        ("2020-01-02 17:00:00.000000100", 100.99, 101.01),
        ("2020-01-02 17:00:00.100000000", 101.50, 101.52),
        ("2020-01-02 17:00:00.200000000", 118.00, 118.02),
    ])
    store = Store(quotes)

    result = run_backtest(frame, params(), execution=NbboExecution(store))

    assert len(result.trades) == 1
    trade = result.trades.iloc[0]
    assert trade.execution_model == "nbbo_top_of_book"
    assert trade.signal_time == pd.Timestamp("2020-01-02 16:45")
    assert trade.order_activation_time == pd.Timestamp("2020-01-02 17:00")
    assert trade.entry_time == pd.Timestamp("2020-01-02 17:00:00.000000100")
    assert trade.entry == pytest.approx(101.01)
    assert trade.entry_bid == pytest.approx(100.99)
    assert trade.entry_ask == pytest.approx(101.01)
    assert trade.entry_spread == pytest.approx(0.02)
    assert trade.reference_entry == pytest.approx(101.0)
    assert trade.entry_slippage_points == pytest.approx(1.0)
    assert trade.reason == "TP" and trade.exit == pytest.approx(118.0)
    assert trade.same_bar_ambiguous is False or not trade.same_bar_ambiguous
    assert store.loaded == [date(2020, 1, 2)]


def test_tick_mode_resolves_m1_same_bar_ambiguity_without_same_bar_rule():
    frame = bars_for_tick_trade(ambiguous=True)
    quotes = quote_day([
        ("2020-01-02 17:00:00.000000100", 100.99, 101.01),
        ("2020-01-02 17:00:00.000000200", 120.00, 120.02),
    ])

    result = run_backtest(frame, params(same_bar_rule="sl_first"), execution=NbboExecution(Store(quotes)))
    trade = result.trades.iloc[0]

    assert trade.reference_reason == "SL"
    assert trade.m1_same_bar_ambiguous
    assert trade.reason == "TP"
    assert not trade.same_bar_ambiguous
    assert "m1_ambiguity_resolved" in set(result.events.event)


def test_tick_mode_is_strict_and_never_falls_back_to_bars():
    frame = bars_for_tick_trade()
    backend = NbboExecution(Store(error=FileNotFoundError("missing exact quote file")))
    with pytest.raises(ValueError, match="NBBO replay failed.*missing_quote_day"):
        run_backtest(frame, params(), execution=backend)


@pytest.mark.parametrize("change,match", [
    ({"entry_mode": "immediate"}, "close-confirmed"),
    ({"eod_close": None}, "end-of-day"),
    ({"breakeven_at_r": 1.0}, "breakeven and trailing"),
    ({"trail_start_r": 1.0}, "breakeven and trailing"),
])
def test_tick_mode_blocks_unvalidated_features(change, match):
    with pytest.raises(ValueError, match=match):
        run_backtest(bars_for_tick_trade(), params(**change), execution=NbboExecution(Store()))
