"""The buffer is selected from prior years before each unseen test year."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from orb_research.config import load_config
from orb_research.walk_forward import (CANDIDATE_BUFFERS, choose_buffer, run_walk_forward,
                                       validate_frozen_config, validate_walk_forward)


BASE = load_config("configs/walk_forward_base.json")


def test_frozen_plan_and_final_holdout_guard():
    assert CANDIDATE_BUFFERS == (0.25, 0.35, 0.45, 0.55)
    validate_frozen_config(BASE)
    with pytest.raises(ValueError, match="development_end"):
        validate_frozen_config(replace(BASE, development_end="2026-01-01",
                                       holdout_start="2026-01-01"))
    m1 = pd.DataFrame({"close": [1.0, 1.0]},
                      index=pd.DatetimeIndex(["2024-12-31", "2025-01-02"], tz="UTC"))
    with pytest.raises(ValueError, match="holdout"):
        validate_walk_forward(BASE, m1)


def test_best_training_mean_r_and_deterministic_tie_break():
    training = pd.DataFrame({"buffer": [0.25, 0.35, 0.45, 0.55],
                             "trades": [100, 100, 100, 0],
                             "mean_r": [-0.02, 0.04, 0.04, None]})
    assert choose_buffer(training) == 0.35


def test_each_test_year_is_scored_after_prior_year_selection(monkeypatch):
    dates = pd.DatetimeIndex([f"{year}-06-01" for year in range(2018, 2025)], tz="UTC")
    m1 = pd.DataFrame({"close": [1.0] * len(dates)}, index=dates)
    observed_tests = []

    def fake_find_signals(prefix, config):
        latest = prefix.index[-1].year
        return ([SimpleNamespace(session_date=f"{year}-06-01")
                 for year in range(2018, latest + 1)], {"incomplete_range": 0})

    def fake_simulate(prefix, signals, config, initial_equity=None):
        if initial_equity is not None:
            observed_tests.append((signals[0].session_date[:4], config.breakout_buffer_atr,
                                   initial_equity))
        equity = config.initial_equity if initial_equity is None else initial_equity
        rows = []
        for signal in signals:
            year = int(signal.session_date[:4])
            if year <= 2020:
                score = 1.0 if config.breakout_buffer_atr == 0.25 else 0.0
            else:
                best = {2021: 0.35, 2022: 0.45, 2023: 0.55, 2024: 0.25}[year]
                score = float((year - 2020) * 10) if config.breakout_buffer_atr == best else 0.0
            pnl = equity * config.risk_percent / 100 * score
            equity += pnl
            rows.append({"R_result": score, "PnL": pnl, "equity_after": equity,
                         "exit_reason": "time", "session_date": signal.session_date})
        return pd.DataFrame(rows), {"signals": len(signals), "skipped": {}}

    monkeypatch.setattr("orb_research.walk_forward.find_signals", fake_find_signals)
    monkeypatch.setattr("orb_research.walk_forward.simulate_signals", fake_simulate)
    result = run_walk_forward(m1, BASE, emit=lambda _: None)
    assert result["folds"].selected_buffer.to_list() == [0.25, 0.35, 0.45, 0.55]
    assert [year for year, _, _ in observed_tests] == ["2021", "2022", "2023", "2024"]
    assert result["oos_trades"].session_date.to_list() == [
        "2021-06-01", "2022-06-01", "2023-06-01", "2024-06-01"]
    assert observed_tests[1][2] == pytest.approx(result["folds"].iloc[0].end_equity)


def test_replication_symbol_allows_one_labelled_instrument_swap():
    """The frozen plan may be replayed on another symbol, but nothing else may move."""
    silver = replace(BASE, symbol="XAGUSD")
    with pytest.raises(ValueError, match="symbol"):
        validate_frozen_config(silver)
    validate_frozen_config(silver, expected_symbol="XAGUSD")
    with pytest.raises(ValueError, match="symbol"):
        validate_frozen_config(BASE, expected_symbol="XAGUSD")
    # a replication cannot quietly change anything else
    with pytest.raises(ValueError, match="range_minutes"):
        validate_frozen_config(replace(silver, range_minutes=30), expected_symbol="XAGUSD")
    with pytest.raises(ValueError, match="session_open"):
        validate_frozen_config(replace(silver, session_open="09:30"), expected_symbol="XAGUSD")
