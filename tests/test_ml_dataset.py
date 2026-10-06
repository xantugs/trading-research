"""Causal feature checks independent of the full broker cache."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from orb_research.config import Config
from orb_research.ml_dataset import EXECUTION_COLUMNS, LABEL_COLUMNS
from orb_research.ml_features import FEATURE_COLUMNS, FeatureContext
from orb_research.ml_report import dictionary_markdown
from orb_research.strategy import Signal
from orb_research.walk_forward import validate_walk_forward


def _sample() -> tuple[pd.DataFrame, Signal]:
    index = pd.date_range("2023-01-03 10:00", "2023-01-03 17:00", freq="min", tz="UTC")
    price = 1800 + np.arange(len(index)) * 0.01
    m1 = pd.DataFrame({"open": price, "high": price + 0.02,
                       "low": price - 0.02, "close": price + 0.005}, index=index)
    start = pd.Timestamp("2023-01-03 13:20", tz="UTC")
    end = start + pd.Timedelta(minutes=60)
    opening = m1.loc[start:end - pd.Timedelta(minutes=1)]
    signal_open = end
    signal_close = end + pd.Timedelta(minutes=5)
    signal = Signal("2023-01-03", "new_york_0820", 1, start, end,
                    float(opening.high.max()), float(opening.low.min()),
                    signal_open, signal_close,
                    float(m1.loc[signal_close - pd.Timedelta(minutes=1), "close"]), 1.5)
    return m1, signal


def test_future_market_mutation_cannot_change_entry_snapshot() -> None:
    m1, signal = _sample()
    original, sources = FeatureContext(m1, 5).snapshot(signal, 0.25)
    changed = m1.copy()
    changed.loc[signal.signal_close + pd.Timedelta(minutes=1):, "close"] += 500
    changed.loc[signal.signal_close + pd.Timedelta(minutes=1):, "high"] += 500
    changed.loc[signal.signal_close + pd.Timedelta(minutes=1):, "low"] += 500
    later, _ = FeatureContext(changed, 5).snapshot(signal, 0.25)
    prefix, _ = FeatureContext(m1.loc[m1.index < signal.signal_close], 5).snapshot(signal, 0.25)
    assert all(source <= signal.signal_close for source in sources.values())
    for name in FEATURE_COLUMNS:
        assert np.isclose(original[name], later[name], equal_nan=True), name
        assert np.isclose(original[name], prefix[name], equal_nan=True), name
    assert original["breakout_distance_price"] > 0
    assert original["pre_or_return_30m"] > 0


def test_missing_pre_or_history_stays_missing_and_labels_are_excluded() -> None:
    m1, signal = _sample()
    m1 = m1.drop(signal.range_start - pd.Timedelta(minutes=15))
    features, _ = FeatureContext(m1, 5).snapshot(signal, 0.25)
    assert np.isnan(features["pre_or_return_30m"])
    assert not set(FEATURE_COLUMNS) & set(LABEL_COLUMNS + EXECUTION_COLUMNS)
    text = dictionary_markdown()
    for name in FEATURE_COLUMNS:
        assert f"| {name} | FEATURE |" in text


def test_holdout_row_is_rejected_before_feature_generation() -> None:
    m1, _ = _sample()
    base = replace(Config(), start="2018-01-01", development_end="2025-01-01",
                   holdout_start="2025-01-01", session_open="08:20", range_minutes=60)
    future = m1.iloc[-1:].copy()
    future.index = pd.DatetimeIndex([pd.Timestamp("2025-01-02", tz="UTC")])
    with pytest.raises(ValueError, match="holdout"):
        validate_walk_forward(base, pd.concat([m1, future]))
