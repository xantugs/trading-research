import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from orb import Params, resample, run_backtest
from orb.config import load_params
from orb.reporting import quality_report, save_result
from orb.synthetic import make_synthetic_m1
from reconcile import parse_ea_header, tf_minutes
from run_all import DEFAULT_DUKASCOPY_START, parse_args

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("change", [
    {"point": 0}, {"risk_pct": float("nan")}, {"same_bar_rule": "unknown"},
    {"eod_close": "25:00"}, {"orb_hour": -1}, {"lot_step": 0},
    {"min_lot": 101}, {"spread": -1}, {"filter_ma_method": "wma"}, {"atr_period": "14"},
    {"use_data_spread": "false"}, {"filter_tf_minutes": 0},
    {"range_multiplier": 0}, {"range_history_sessions": 0}, {"range_percentile": 101},
    {"premarket_window_minutes": 0}, {"premarket_sensitivity": -1}, {"confirmation_bars": 0},
    {"filter_ma_period": 0, "filter_ma_slope_bars": 2, "filter_ma_min_slope_pct": 0.1},
])
def test_parameter_validation(change):
    with pytest.raises(ValueError):
        Params(**change).validate(15)


def test_typed_overrides_and_unknown_keys():
    p = load_params(overrides=["use_data_spread=false", "eod_close=null", "tp_ratio=2.25"])
    assert p.use_data_spread is False and p.eod_close is None and p.tp_ratio == 2.25
    with pytest.raises(ValueError, match="Unknown"):
        load_params(overrides=["tp_rato=2"])


def test_extended_header():
    p = parse_ea_header("# ORB EA v1.30 tf=PERIOD_M15 orb=16:30 ma=0/PERIOD_H1/MODE_EMA "
                        "atr_period=20 vol_lookback=6 vol_spike_min=2 point_value=3 initial_balance=5000")
    assert p["atr_period"] == 20 and p["vol_spike_min"] == 2
    assert p["point_value"] == 3 and p["initial_balance"] == 5000
    with pytest.raises(ValueError):
        tf_minutes("TYPO")
    with pytest.raises(ValueError):
        parse_ea_header("# ma=20/PERIOD_H1/MODE_LWMA")


def test_no_trade_schema_and_day_count():
    m1 = make_synthetic_m1(days=3, seed=5)
    result = run_backtest(resample(m1, 15), Params(vol_spike_min=10000), path=m1)
    assert result.trades.empty
    assert {"date", "direction", "entry_time", "r", "lots"} <= set(result.trades)
    assert result.stats["days_traded"] == 0


def test_report_strict_json_and_no_overwrite(tmp_path):
    m1 = make_synthetic_m1(days=3, seed=5)
    result = run_backtest(resample(m1, 15), Params(), path=m1)
    result.stats["profit_factor"] = float("inf")
    target = tmp_path / "result"
    report = save_result(result, target, {"label": "<script>bad</script>"}, {})
    assert report.exists()
    assert json.loads((target / "stats.json").read_text())["profit_factor"] is None
    assert "<script>bad</script>" not in report.read_text(encoding="utf-8")
    assert "source_sha256" in json.loads((target / "manifest.json").read_text())
    with pytest.raises(FileExistsError):
        save_result(result, target, {}, {})


def test_quality_flags_missing_opening_minute():
    m1 = make_synthetic_m1(days=3, seed=5)
    target = m1.index[(m1.index.hour == 16) & (m1.index.minute == 35)][0]
    quality = quality_report(m1.drop(target), 15, 16, 30)
    assert str(target.date()) in quality["incomplete_or_missing_opening_ranges"]


def test_launcher_from_another_working_directory(tmp_path):
    env = os.environ.copy()
    # Keep the running app's demo dataset untouched during the CLI smoke test.
    bars = tmp_path / "SYNTHETIC_TEST_M1.csv"
    make_synthetic_m1(days=3, seed=42).to_csv(bars)
    result = subprocess.run([sys.executable, str(ROOT / "run_all.py"), "--no-install", "--no-app",
                             "--source", "csv", "--bars", str(bars), "--output", str(tmp_path / "bundle")],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "bundle/report.html").exists()
    assert (tmp_path / "bundle/trades.csv").exists()


def test_dukascopy_one_flag_has_research_defaults():
    args = parse_args(["--source", "dukascopy"])
    assert args.start == DEFAULT_DUKASCOPY_START
    assert args.symbol == "USATECHIDXUSD"
    assert args.tz == "ny+7"
    assert args.end


def test_auto_source_with_explicit_bars_becomes_csv():
    args = parse_args(["--source", "auto", "--bars", "data/ANY_M1.csv"])
    assert args.source == "csv"
    assert args.bars == Path("data/ANY_M1.csv")


def test_wrds_source_defaults_to_qqq():
    args = parse_args(["--source", "wrds", "--bars", "data/wrds/years"])
    assert args.symbol == "QQQ"


def test_data_only_prepares_local_bars_without_report(tmp_path):
    bars = tmp_path / "LOCAL_M1.csv"
    make_synthetic_m1(days=3, seed=42).to_csv(bars)
    result = subprocess.run(
        [sys.executable, str(ROOT / "run_all.py"), "--no-install", "--data-only",
         "--source", "csv", "--bars", str(bars)],
        cwd=tmp_path, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Data ready:" in result.stdout


def test_no_signals_not_counted_as_traded():
    index = pd.date_range("2025-05-06 16:00", periods=12, freq="15min")
    frame = pd.DataFrame({"open": 95, "high": 100, "low": 90, "close": 95}, index=index)
    result = run_backtest(frame, Params())
    assert result.stats["days_traded"] == 0
    assert result.days.iloc[0]["status"] == "no signal"
