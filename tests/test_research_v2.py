"""Independent prices for signal timing, fill ordering and data boundaries."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from orb_research.backtester import backtest
from orb_research.config import Config
from orb_research.data_loader import validate_m1
from orb_research.report import metrics, write_baseline
from orb_research.strategy import find_signals
from orb_research.monte_carlo import trade_sequence_monte_carlo
from orb_research.prop_simulator import PropRules, simulate_prop
from orb_research.statistics import confidence_intervals


def _day(day: str, *, both_levels_on_entry: bool = False) -> pd.DataFrame:
    ny = ZoneInfo("America/New_York")
    local = datetime.fromisoformat(f"{day}T09:30:00").replace(tzinfo=ny)
    first = pd.Timestamp(local).tz_convert("UTC")
    index = pd.date_range(first, first + timedelta(minutes=391), freq="min", tz="UTC", inclusive="left")
    frame = pd.DataFrame({
        "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
        "tick_volume": 100, "spread": 20, "real_volume": 0,
    }, index=index)
    frame.index.name = "time"
    opening = frame.loc[first:first + timedelta(minutes=29)]
    frame.loc[opening.index, "high"] = 101.0
    frame.loc[opening.index, "low"] = 99.0
    signal_time = first + timedelta(minutes=34)
    frame.loc[signal_time, ["high", "close"]] = [102.0, 102.0]
    entry_time = first + timedelta(minutes=35)
    frame.loc[entry_time, ["open", "high", "low", "close"]] = [102.0, 102.5, 101.5, 102.0]
    exit_time = first + timedelta(minutes=36)
    if both_levels_on_entry:
        frame.loc[entry_time, ["high", "low"]] = [110.0, 98.0]
    else:
        frame.loc[exit_time, ["open", "high", "low", "close"]] = [102.0, 110.0, 101.5, 109.0]
    return frame


def _config(**changes) -> Config:
    return replace(Config(), expected_server="FTMO-Demo", start="2024-01-01",
                   tick_start="2024-01-01",
                   development_end="2024-04-01", holdout_start="2025-01-01", **changes)


def test_new_york_dst_and_next_bar_entry():
    m1 = pd.concat([_day("2024-03-08"), _day("2024-03-11")])
    signals, _ = find_signals(m1, _config())
    assert [str(s.range_start) for s in signals] == [
        "2024-03-08 14:30:00+00:00", "2024-03-11 13:30:00+00:00",
    ]
    assert all(s.signal_close == s.range_start + timedelta(minutes=35) for s in signals)
    trades, diagnostics = backtest(m1, _config())
    assert diagnostics["trades"] == 2
    assert (trades.timestamp == trades.signal_close).all()
    assert (trades.exit_reason == "target").all()
    assert (trades.direction == "long").all()
    assert (trades.position_size > 0).all()


def test_same_m1_stop_and_target_is_a_loss():
    m1 = _day("2024-01-02", both_levels_on_entry=True)
    trades, _ = backtest(m1, _config())
    assert len(trades) == 1
    assert trades.iloc[0].exit_reason == "stop"
    assert bool(trades.iloc[0].ambiguous_m1)
    assert trades.iloc[0].R_result < 0
    assert trades.iloc[0].timestamp == trades.iloc[0].exit_time


def test_holdout_is_cut_before_strategy_and_report(tmp_path):
    config = _config(output_dir=str(tmp_path))
    data = pd.concat([_day("2024-01-02"), _day("2025-01-02")])
    data = validate_m1(data, config)
    assert data.index.max().year == 2024
    trades, diagnostics = backtest(data, config)
    assert len(trades) == 1
    summary, monthly, yearly = metrics(trades, config)
    assert summary["trades"] == 1
    assert len(monthly) == 1 and len(yearly) == 1
    report = write_baseline(trades, diagnostics, {"company": "FTMO", "server": config.expected_server,
                                                   "symbol": config.symbol}, config)
    assert report.exists()
    assert (tmp_path / "trades.csv").exists()
    assert "development data only" in report.read_text(encoding="utf-8")


def test_config_rejects_time_leakage():
    with pytest.raises(ValueError, match="holdout"):
        replace(_config(), development_end="2026-01-01").validate()


def test_monte_carlo_and_prop_replay_are_seeded_and_censored():
    m1 = pd.concat([_day("2024-03-08"), _day("2024-03-11")])
    trades, _ = backtest(m1, _config())
    one, paths = trade_sequence_monte_carlo(trades, 0.5, paths=10_000, seed=4)
    two, _ = trade_sequence_monte_carlo(trades, 0.5, paths=10_000, seed=4)
    pd.testing.assert_frame_equal(one, two)
    assert len(paths) == 20_000
    assert set(one.method) == {"reshuffle", "bootstrap"}
    replay = simulate_prop(trades, PropRules(), risk_levels=(0.25, 0.5))
    assert set(replay.risk_percent) == {0.25, 0.5}
    assert set(replay.phase1) == {"censored"}
    intervals = confidence_intervals(trades, seed=4)
    assert (intervals.lower_95 <= intervals.estimate).all()
    assert (intervals.upper_95 >= intervals.estimate).all()


def test_one_command_synthetic_smoke(tmp_path):
    data = _day("2024-01-02")
    data_path = tmp_path / "synthetic_m1.parquet"
    data.to_parquet(data_path)
    data_path.with_suffix(".json").write_text(json.dumps({
        "company": "Synthetic test", "server": "FTMO-Demo",
        "symbol": "XAUUSD", "chart_mode": "bid",
    }), encoding="utf-8")
    config = replace(_config(), source="parquet", data_path=str(data_path),
                     output_dir=str(tmp_path / "report"))
    from orb_research.config import serialise
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(serialise(config)), encoding="utf-8")
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(project / "main.py"), "--config", str(config_path)],
                            cwd=project, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "report" / "trades.csv").exists()
    assert (tmp_path / "report" / "summary_report.md").exists()
    assert (tmp_path / "report" / "equity_curve.html").exists()


def test_optional_analysis_command_writes_reviewable_outputs(tmp_path):
    data = pd.concat([_day("2024-03-08"), _day("2024-03-11")])
    data_path = tmp_path / "synthetic_m1.parquet"
    data.to_parquet(data_path)
    data_path.with_suffix(".json").write_text(json.dumps({
        "company": "Synthetic test", "server": "FTMO-Demo",
        "symbol": "XAUUSD", "chart_mode": "bid",
    }), encoding="utf-8")
    config = replace(_config(), source="parquet", data_path=str(data_path),
                     output_dir=str(tmp_path / "analysis"))
    from orb_research.config import serialise
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(serialise(config)), encoding="utf-8")
    prop_path = tmp_path / "prop.json"
    prop_path.write_text(json.dumps({"reset_timezone": "UTC"}), encoding="utf-8")
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(project / "main.py"), "--config", str(config_path),
                             "--no-plots", "--post-baseline-analysis", "--prop-rules", str(prop_path)],
                            cwd=project, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    for filename in ("monte_carlo_summary.csv", "confidence_intervals.csv",
                     "prop_simulation_summary.csv", "cost_sensitivity.csv"):
        assert (tmp_path / "analysis" / filename).exists()
