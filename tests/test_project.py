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
from run_all import (
    DEFAULT_WRDS_M1_END,
    DEFAULT_WRDS_M1_START,
    DEFAULT_DUKASCOPY_START, DEFAULT_WRDS_M1_ROOT, DEFAULT_WRDS_TICK_ROOT, parse_args,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("change", [
    {"point": 0}, {"risk_pct": float("nan")}, {"same_bar_rule": "unknown"},
    {"eod_close": "25:00"}, {"orb_hour": -1}, {"lot_step": 0},
    {"min_lot": 101}, {"spread": -1}, {"filter_ma_method": "wma"}, {"atr_period": "14"},
    {"use_data_spread": "false"}, {"filter_tf_minutes": 0},
    {"range_multiplier": 0}, {"range_history_sessions": 0}, {"range_percentile": 101},
    {"premarket_window_minutes": 0}, {"premarket_sensitivity": -1}, {"confirmation_bars": 0},
    {"filter_ma_period": 0, "filter_ma_slope_bars": 2, "filter_ma_min_slope_pct": 0.1},
    {"max_spread": -1}, {"min_stop_points": -1}, {"max_gross_leverage": -1},
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
    p = parse_ea_header("# ORB EA v1.40 tf=PERIOD_M15 orb=16:30 ma=0/PERIOD_H1/MODE_EMA "
                        "atr_period=20 vol_lookback=6 vol_spike_min=2 point=0.01 point_value=3 "
                        "initial_balance=5000 range_mode=historical_percentile range_sessions=30 "
                        "range_percentile=60 range_multiplier=1.25 premarket_minutes=120 "
                        "premarket_sensitivity=.5 confirmation_bars=3 confirmation_color=true "
                        "stop_mode=midpoint range_lookback_minutes=90 max_range_lookback_ratio=.25 "
                        "ma_slope_bars=2 ma_min_slope_pct=.05 max_spread_points=20 min_stop_points=50")
    assert p["atr_period"] == 20 and p["vol_spike_min"] == 2
    assert p["point_value"] == 3 and p["initial_balance"] == 5000
    assert p["range_mode"] == "historical_percentile" and p["range_history_sessions"] == 30
    assert p["confirmation_bars"] == 3 and p["confirmation_same_color"] is True
    assert p["stop_mode"] == "midpoint" and p["max_spread"] == pytest.approx(.2)
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
    report = save_result(result, target, {
        "label": "<script>bad</script>",
        "execution_model": "nbbo_top_of_book",
        "execution_strict": False,
    }, {})
    assert report.exists()
    assert json.loads((target / "stats.json").read_text())["profit_factor"] is None
    assert "<script>bad</script>" not in report.read_text(encoding="utf-8")
    manifest = json.loads((target / "manifest.json").read_text())
    assert "source_sha256" in manifest
    assert any("recorded and skipped" in note for note in manifest["limitations"])
    assert (target / "events.csv").exists()
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
    assert "100% 4/4 backtest and report complete" in result.stdout


def test_dukascopy_one_flag_has_research_defaults():
    args = parse_args(["--source", "dukascopy"])
    assert args.start == DEFAULT_DUKASCOPY_START
    assert args.symbol == "USATECHIDXUSD"
    assert args.tz == "ny+7"
    assert args.end == pd.Timestamp.now(tz="UTC").date().isoformat()


def test_auto_source_with_explicit_bars_becomes_csv():
    args = parse_args(["--source", "auto", "--bars", "data/ANY_M1.csv"])
    assert args.source == "csv"
    assert args.bars == Path("data/ANY_M1.csv")


def test_wrds_source_defaults_to_qqq():
    args = parse_args(["--source", "wrds", "--bars", "data/wrds/years"])
    assert args.symbol == "QQQ"
    assert args.config == ROOT / "configs/qqq.json"
    defaulted = parse_args(["--source", "wrds"])
    assert defaulted.mode == "m1" and defaulted.bars == DEFAULT_WRDS_M1_ROOT


def test_launcher_no_arguments_runs_fast_wrds_qqq_m1_mode():
    args = parse_args([])
    assert args.source == "wrds" and args.mode == "m1" and args.app_mode is None
    assert args.symbol == "QQQ" and args.bars == DEFAULT_WRDS_M1_ROOT
    assert args.start == DEFAULT_WRDS_M1_START and args.end == DEFAULT_WRDS_M1_END
    assert parse_args(["--source", "auto"]).source == "wrds"


def test_wrds_modes_have_separate_default_roots():
    m1 = parse_args(["--mode", "m1"])
    assert m1.source == "wrds" and m1.mode == "m1"
    assert m1.bars == DEFAULT_WRDS_M1_ROOT and m1.tick_root is None

    tick = parse_args(["--mode", "tick"])
    assert tick.source == "wrds" and tick.mode == "tick"
    assert tick.tick_root == DEFAULT_WRDS_TICK_ROOT and tick.bars is None
    assert tick.start is None and tick.end is None


def test_wrds_modes_reject_overlapping_input_roots():
    with pytest.raises(SystemExit):
        parse_args(["--mode", "tick", "--bars", "C:/data/wrds_m1"])
    with pytest.raises(SystemExit):
        parse_args(["--mode", "m1", "--tick-root", "C:/data/wrds_ticks"])
    with pytest.raises(SystemExit):
        parse_args(["--mode", "m1", "--bars", str(DEFAULT_WRDS_TICK_ROOT)])
    with pytest.raises(SystemExit):
        parse_args(["--mode", "tick", "--tick-root", str(DEFAULT_WRDS_M1_ROOT)])
    with pytest.raises(SystemExit):
        parse_args(["--mode", "m1", "--bars", str(DEFAULT_WRDS_TICK_ROOT / "2020")])


def test_launcher_rejects_invalid_or_reversed_date_bounds():
    with pytest.raises(SystemExit):
        parse_args(["--mode", "m1", "--from", "2020/01/01"])
    with pytest.raises(SystemExit):
        parse_args(["--mode", "m1", "--from", "2020-02-01", "--to", "2020-02-01"])


def test_qqq_config_uses_equity_and_recorded_spread_semantics():
    params = load_params(ROOT / "configs/qqq.json")
    params.validate(15)
    assert params.use_data_spread is True and params.spread == 0
    assert params.point == pytest.approx(0.01) and params.point_value == 1
    assert params.lot_step == 1 and params.min_lot == 1
    assert params.max_gross_leverage == 1


def test_recorded_spread_mode_rejects_an_all_zero_input(tmp_path):
    bars = tmp_path / "ZERO_SPREAD_M1.csv"
    make_synthetic_m1(days=3, seed=42).assign(spread=0).to_csv(bars)
    result = subprocess.run(
        [sys.executable, str(ROOT / "run_all.py"), "--no-install", "--no-app",
         "--source", "csv", "--bars", str(bars), "--config", str(ROOT / "configs/qqq.json")],
        cwd=tmp_path, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 1
    assert "no positive recorded spreads" in result.stderr


def test_wrds_manifest_symbol_mismatch_is_rejected(tmp_path):
    bars = tmp_path / "QQQ_2025_M1.csv.gz"
    make_synthetic_m1(days=3, seed=42).to_csv(bars, compression="gzip")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "symbol": "SPY", "file": bars.name,
        "strategy_clock": "New York wall time + 7 hours", "point": 0.01,
    }))
    result = subprocess.run(
        [sys.executable, str(ROOT / "run_all.py"), "--no-install", "--no-app", "--data-only",
         "--source", "wrds", "--bars", str(bars)],
        cwd=tmp_path, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 1
    assert "manifest symbol mismatch" in result.stderr


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
    assert "100% 3/3 data validated" in result.stdout


def test_no_signals_not_counted_as_traded():
    index = pd.date_range("2025-05-06 16:00", periods=12, freq="15min")
    frame = pd.DataFrame({"open": 95, "high": 100, "low": 90, "close": 95}, index=index)
    result = run_backtest(frame, Params())
    assert result.stats["days_traded"] == 0
    assert result.days.iloc[0]["status"] == "no signal"
