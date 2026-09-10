from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from orb.execution import (
    ExecutionDataError,
    ExecutionIntent,
    NbboExecution,
    NbboExecutionConfig,
    replay_nbbo,
)
from orb.ticks import QuoteDay, TickCoverage


DAY = date(2020, 1, 2)


def quote_day(rows, *, offset=7):
    times = np.array([pd.Timestamp(row[0]).to_datetime64() for row in rows], dtype="datetime64[ns]")
    bids = np.array([row[1] for row in rows], dtype=float)
    asks = np.array([row[2] for row in rows], dtype=float)
    coverage = TickCoverage(
        source_date=DAY,
        source_path=Path("QQQ_2020-01-02_quotes.parquet"),
        source_rows=len(rows), rows=len(rows), dropped_timestamps=0,
        dropped_invalid=0, dropped_crossed=0,
        duplicate_timestamps=int(pd.Index(times).duplicated(keep=False).sum()),
        first=pd.Timestamp(times[0]) if len(times) else None,
        last=pd.Timestamp(times[-1]) if len(times) else None,
        strategy_offset_hours=offset,
    )
    return QuoteDay(times, bids, asks, coverage)


def intent(**changes):
    values = dict(
        direction=1,
        signal_time=pd.Timestamp("2020-01-02 16:44:00"),
        activation_time=pd.Timestamp("2020-01-02 16:45:00"),
        stop_loss=98.0,
        target_ratio=1.5,
        eod_time=pd.Timestamp("2020-01-02 22:45:00"),
        point=0.01,
        reference_entry=99.99,
    )
    values.update(changes)
    return ExecutionIntent(**values)


def test_long_enters_ask_exits_bid_and_measures_exact_excursions():
    quotes = quote_day([
        ("2020-01-02 16:44:59.999999900", 99.98, 99.99),
        ("2020-01-02 16:45:00.000000100", 99.99, 100.00),
        ("2020-01-02 16:45:00.100000000", 99.50, 99.51),
        ("2020-01-02 16:45:00.200000000", 101.00, 101.01),
        ("2020-01-02 16:45:00.300000000", 103.20, 103.21),
    ])

    result = replay_nbbo(intent(), quotes)

    assert result.executed and result.exit_reason == "TP"
    assert result.entry_event_id == 1 and result.entry == 100.0
    assert result.entry_bid == 99.99 and result.entry_ask == 100.0
    assert result.entry_time == pd.Timestamp("2020-01-02 16:45:00.000000100")
    assert result.target == pytest.approx(103.0)
    assert result.exit_event_id == 4 and result.exit == pytest.approx(103.2)
    assert result.exit_slippage_points == pytest.approx(-20.0)
    assert result.r_multiple == pytest.approx(1.6)
    assert result.mfe_r == pytest.approx(1.6)
    assert result.mae_r == pytest.approx(0.25)
    assert result.mfe_event_id == 4 and result.mae_event_id == 2
    assert result.mfe_time == pd.Timestamp("2020-01-02 16:45:00.300")
    assert result.mae_price == 99.5
    assert result.quote_events_processed == 4


def test_short_enters_bid_and_exits_ask():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 100.00, 100.01),
        ("2020-01-02 16:45:01", 98.50, 98.51),
        ("2020-01-02 16:45:02", 96.70, 96.80),
    ])
    setup = intent(direction=-1, stop_loss=102.0, reference_entry=100.01)

    result = replay_nbbo(setup, quotes)

    assert result.entry == 100.0
    assert result.exit_reason == "TP" and result.exit == 96.8
    assert result.target == 97.0
    assert result.r_multiple == pytest.approx(1.6)
    assert result.entry_slippage_points == pytest.approx(1.0)
    assert result.mfe_price == 96.8
    assert result.mae_price == 100.01


def test_quote_jump_through_stop_fills_observed_side_and_records_slippage():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 16:45:00.010", 97.50, 97.51),
    ])

    result = replay_nbbo(intent(), quotes)

    assert result.exit_reason == "SL"
    assert result.exit == 97.5
    assert result.exit_trigger_level == 98.0
    assert result.exit_slippage_points == pytest.approx(50.0)
    assert result.r_multiple == pytest.approx(-1.25)
    assert result.mae_r == pytest.approx(1.25)


def test_duplicate_timestamp_order_is_preserved_by_event_id():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 16:45:00", 97.90, 97.91),
        ("2020-01-02 16:45:00", 104.00, 104.01),
    ])

    result = replay_nbbo(intent(), quotes)

    assert result.entry_time == result.exit_time
    assert result.entry_event_id == 0 and result.exit_event_id == 1
    assert result.exit_reason == "SL"
    assert result.quote_events_processed == 2


def test_eod_has_priority_over_a_simultaneous_target_touch():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 22:45:00.000500", 104.00, 104.01),
    ])

    result = replay_nbbo(intent(), quotes)

    assert result.exit_reason == "EOD"
    assert result.exit == 104.0
    assert result.exit_trigger_level is None
    assert result.exit_slippage_points is None
    assert result.eod_delay_ms == pytest.approx(0.5)


def test_strict_entry_delay_raises_and_nonstrict_returns_unreplayable():
    quotes = quote_day([
        ("2020-01-02 16:45:02", 99.99, 100.00),
        ("2020-01-02 22:45:00", 100.00, 100.01),
    ])
    strict = NbboExecutionConfig(max_entry_delay_ms=1_000)
    with pytest.raises(ExecutionDataError, match="2000.000 ms") as caught:
        replay_nbbo(intent(), quotes, strict)
    assert caught.value.code == "entry_quote_delayed"

    loose = NbboExecutionConfig(max_entry_delay_ms=1_000, strict=False)
    result = replay_nbbo(intent(), quotes, loose)
    assert result.status == "unreplayable"
    assert "entry_quote_delayed" in result.detail
    assert result.entry is None


def test_strict_eod_delay_and_data_end_never_create_end_mark():
    late = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 22:45:02", 100.00, 100.01),
    ])
    with pytest.raises(ExecutionDataError) as caught:
        replay_nbbo(intent(), late, NbboExecutionConfig(max_eod_delay_ms=1_000))
    assert caught.value.code == "eod_quote_delayed"

    ended = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 17:00:00", 100.00, 100.01),
    ])
    with pytest.raises(ExecutionDataError) as caught:
        replay_nbbo(intent(), ended)
    assert caught.value.code == "quote_data_ended"


def test_optional_interquote_gap_is_strict():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 16:45:01", 100.00, 100.01),
    ])
    with pytest.raises(ExecutionDataError) as caught:
        replay_nbbo(intent(), quotes, NbboExecutionConfig(max_interquote_gap_ms=500))
    assert caught.value.code == "interquote_gap"


def test_actual_quote_guards_reject_without_fabricating_a_trade():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 99.00, 100.00),
        ("2020-01-02 22:45:00", 100.00, 100.01),
    ])
    wide = replay_nbbo(intent(max_spread=0.05), quotes)
    assert wide.status == "rejected" and "spread_too_wide" in wide.detail
    assert wide.entry == 100.0 and wide.exit is None

    beyond = replay_nbbo(intent(stop_loss=100.01), quotes)
    assert beyond.status == "rejected" and "entry_beyond_stop" in beyond.detail

    close = replay_nbbo(intent(min_stop_distance=2.01), quotes)
    assert close.status == "rejected" and "stop_too_close" in close.detail


@pytest.mark.parametrize("change,match", [
    ({"entry_mode": "immediate"}, "close-confirmed"),
    ({"breakeven_at_r": 1.0}, "breakeven"),
    ({"trail_start_r": 1.0}, "trailing"),
])
def test_milestone_rejects_unsupported_execution_modes(change, match):
    with pytest.raises(NotImplementedError, match=match):
        intent(**change)


class FakeStore:
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


def test_backend_maps_strategy_clock_to_source_date_and_missing_is_strict():
    quotes = quote_day([
        ("2020-01-02 16:45:00", 99.99, 100.00),
        ("2020-01-02 22:45:00", 100.00, 100.01),
    ])
    store = FakeStore(quotes)
    result = NbboExecution(store).execute(intent())
    assert result.exit_reason == "EOD"
    assert store.loaded == [DAY]

    missing = NbboExecution(FakeStore(error=FileNotFoundError("gone")))
    with pytest.raises(ExecutionDataError) as caught:
        missing.execute(intent())
    assert caught.value.code == "missing_quote_day"


def test_latency_uses_first_quote_at_or_after_delayed_activation():
    quotes = quote_day([
        ("2020-01-02 16:45:00.000", 99.89, 99.90),
        ("2020-01-02 16:45:00.005", 99.99, 100.00),
        ("2020-01-02 22:45:00.000", 100.00, 100.01),
    ])
    result = replay_nbbo(intent(), quotes, NbboExecutionConfig(latency_ms=5))
    assert result.order_activation_time == pd.Timestamp("2020-01-02 16:45:00.005")
    assert result.entry_event_id == 1 and result.entry == 100.0
    assert result.entry_delay_ms == 0.0
