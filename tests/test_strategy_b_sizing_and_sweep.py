"""Prespecified selection and minimum-lot diagnostics without real market data."""
from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from orb_research.backtester import minimum_lot_feasibility
from orb_research.config import load_config
from orb_research.risk import size_position
from orb_research.strategy import Signal
from orb_research.strategy_b_sweep import choose_cell, run_sweep, write_results


BASE = load_config("configs/strategy_b_0820.json")


def test_5k_feasibility_is_reported_without_filtering_research_signals():
    stamp = pd.Timestamp("2024-02-13 13:25", tz="UTC")
    bar = pd.DataFrame({"open": [100.0], "spread": [20]}, index=pd.DatetimeIndex([stamp]))
    signal = Signal("2024-02-13", "true_new_york_0820", 1,
                    stamp - pd.Timedelta(minutes=65), stamp - pd.Timedelta(minutes=5),
                    100.0, 50.0, stamp - pd.Timedelta(minutes=5), stamp,
                    100.0, 100.0)
    signals = [signal]
    before = tuple(signals)
    diagnostic = minimum_lot_feasibility(bar, signals, replace(BASE, initial_equity=100_000))
    assert diagnostic == {"signals": 1, "valid_sizing_signals": 1,
                          "invalid_entry_or_gap": 0, "infeasible_at_5k": 1,
                          "infeasible_share": 1.0}
    assert size_position(5000, 100.22, 50.0, BASE)[0] == 0
    assert size_position(100_000, 100.22, 50.0, BASE)[0] >= BASE.min_lot
    assert tuple(signals) == before  # diagnostic has not removed it


def test_eligibility_and_tie_break_are_training_only():
    surface = pd.DataFrame([
        {"range_minutes": 15, "buffer": 0.25, "trades": 99, "mean_r": 9.0},
        {"range_minutes": 30, "buffer": 0.35, "trades": 100, "mean_r": 1.0},
        {"range_minutes": 15, "buffer": 0.55, "trades": 100, "mean_r": 1.0},
        {"range_minutes": 15, "buffer": 0.35, "trades": 100, "mean_r": 1.0},
    ])
    assert choose_cell(surface, 100) == (15, 0.35)
    with pytest.raises(ValueError, match="no 4×4 cell"):
        choose_cell(surface, 101)


def test_only_selected_cell_reaches_each_unseen_year(monkeypatch, tmp_path):
    dates = pd.DatetimeIndex([f"{year}-06-01" for year in range(2018, 2025)], tz="UTC")
    bars = pd.DataFrame({"close": [1.0] * len(dates)}, index=dates)
    test_calls = []

    def fake_signals(prefix, config):
        latest = prefix.index[-1].year
        return [SimpleNamespace(session_date=f"{year}-06-01")
                for year in range(2018, latest + 1)], {}

    def fake_simulate(prefix, signals, config, initial_equity=None):
        if initial_equity is not None:
            test_calls.append((int(signals[0].session_date[:4]), config.range_minutes,
                               config.breakout_buffer_atr, prefix.index.max()))
        equity = config.initial_equity if initial_equity is None else initial_equity
        rows = []
        for signal in signals:
            year = int(signal.session_date[:4])
            score = 0.6 if (config.range_minutes, config.breakout_buffer_atr) == (15, 0.25) else 0.1
            pnl = equity * config.risk_percent / 100 * score
            equity += pnl
            rows.append({"session_date": signal.session_date, "R_result": score,
                         "PnL": pnl, "equity_after": equity, "exit_reason": "time",
                         "direction": "long" if year % 2 else "short",
                         "exit_time": pd.Timestamp(f"{year}-06-01", tz="UTC")})
        return pd.DataFrame(rows), {"signals": len(signals), "skipped": {}}

    monkeypatch.setattr("orb_research.strategy_b_sweep.find_signals", fake_signals)
    monkeypatch.setattr("orb_research.strategy_b_sweep.simulate_signals", fake_simulate)
    monkeypatch.setattr("orb_research.strategy_b_sweep.minimum_lot_feasibility",
                        lambda _bars, signals, _config: {"infeasible_at_5k": 0,
                                                         "valid_sizing_signals": len(signals),
                                                         "infeasible_share": 0.0})
    config = replace(BASE, initial_equity=100_000, min_trades_for_inference=1)
    result = run_sweep(bars, config, emit=lambda _: None)
    assert len(result["training_surface"]) == 64
    assert len(test_calls) == 4  # no all-cell OOS surface
    assert all(row.eligible for row in result["training_surface"].itertuples(index=False))
    for call, row in zip(test_calls, result["selected_path"].itertuples(index=False)):
        assert call[:3] == (row.test_year, row.selected_or_minutes, row.selected_buffer)
        assert call[3] < pd.Timestamp(f"{row.test_year + 1}-01-01", tz="UTC")
    assert result["oos_trades"].session_date.max() < "2025-01-01"
    report = write_results(tmp_path / "sweep", result, config, {"server": "FTMO-Demo"})
    assert report.is_file()
    assert (report.parent / "training_surface.csv").is_file()
    assert (report.parent / "stability.md").is_file()
    assert "holdout_used\": false" in (report.parent / "spec.json").read_text(encoding="utf-8")


def test_sweep_rejects_any_2025_input_before_scanning(monkeypatch):
    bars = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex(["2025-01-01"], tz="UTC"))
    monkeypatch.setattr("orb_research.strategy_b_sweep.find_signals",
                        lambda *_: pytest.fail("must not scan holdout"))
    with pytest.raises(ValueError, match="final holdout"):
        run_sweep(bars, replace(BASE, initial_equity=100_000), emit=lambda _: None)
