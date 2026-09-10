from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import orb
from orb import NbboExecution
from orb.synthetic import make_synthetic_m1


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def app(tmp_path, monkeypatch):
    path = tmp_path / "DEMO_M1.csv"
    make_synthetic_m1(days=4, seed=9).to_csv(path)
    monkeypatch.setenv("ORB_DATA_FILE", str(path))
    monkeypatch.setenv("ORB_BACKTEST_MODE", "m1")
    monkeypatch.delenv("ORB_PARAMS_FILE", raising=False)
    for name in (
        "ORB_TICK_ROOT", "ORB_TICK_LATENCY_MS", "ORB_TICK_MAX_ENTRY_DELAY_MS",
        "ORB_TICK_MAX_EOD_DELAY_MS", "ORB_TICK_MAX_INTERQUOTE_GAP_MS",
        "ORB_TICK_OFFSET_HOURS", "ORB_TICK_CACHE_DAYS", "ORB_TICK_SYMBOL",
        "ORB_TICK_STRICT",
    ):
        monkeypatch.delenv(name, raising=False)
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_app_load_and_backtest(app):
    assert not app.exception
    assert app.session_state["backtest_mode"] == "m1"
    assert any("M1 BAR REPLAY" in item.value for item in app.markdown)
    assert [tab.label for tab in app.tabs] == [
        "Overview", "Trades", "Validation", "Compare", "Algorithm", "Data quality", "Diagnostics"]
    labels = {metric.label for metric in app.metric}
    assert {"Net return", "Win rate", "Average R", "Max drawdown"} <= labels
    assert {"EA source", "Selected setup", "Latest local compile"} <= labels
    button(app, "Run backtest").click().run(timeout=30)
    assert not app.exception
    assert {"Net return", "Win rate", "Average R", "Max drawdown"} <= {
        metric.label for metric in app.metric}


def test_pending_settings_and_saved_snapshots(app):
    original = app.session_state["active_run"]["result"].params.tp_ratio
    app.selectbox(key="preset").select("2R target").run(timeout=30)
    assert not app.exception
    assert app.session_state["active_run"]["result"].params.tp_ratio == original
    assert any("Setup changed" in item.value for item in app.info)
    button(app, "Save run").click().run(timeout=30)
    first = app.session_state["experiments"][0]
    assert first["result"].params.tp_ratio == original
    assert first["name"] == "01 · Baseline"
    button(app, "Run backtest").click().run(timeout=30)
    button(app, "Save run").click().run(timeout=30)
    assert not app.exception
    saved = app.session_state["experiments"]
    assert len(saved) == 2
    assert saved[0]["result"].params.tp_ratio == original
    assert saved[1]["result"].params.tp_ratio == 2.0
    button(app, "Clear saved runs").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["experiments"] == []


def test_auto_run_and_explorer_filters(app):
    app.toggle(key="auto_run").set_value(True).run(timeout=30)
    app.slider(key="p_tp_ratio").set_value(2.5).run(timeout=30)
    assert not app.exception
    assert app.session_state["active_run"]["result"].params.tp_ratio == 2.5
    assert app.session_state["experiments"] == []
    run_id = app.session_state["active_run"]["id"]
    direction = next(item for item in app.multiselect if item.label == "Direction filter")
    direction.set_value([]).run(timeout=30)
    assert not app.exception
    assert any("No trades match" in item.value for item in app.info)
    assert app.session_state["active_run"]["id"] == run_id
    direction = next(item for item in app.multiselect if item.label == "Direction filter")
    direction.set_value(["short"]).run(timeout=30)
    assert not app.exception
    assert app.session_state["active_run"]["id"] == run_id


def test_algorithm_tab_reports_selected_mt5_parity(app):
    readiness = next(metric for metric in app.metric if metric.label == "Selected setup")
    assert readiness.value == "MT5-ready"
    app.selectbox(key="preset").select("Historical range").run(timeout=30)
    button(app, "Run backtest").click().run(timeout=30)
    assert not app.exception
    readiness = next(metric for metric in app.metric if metric.label == "Selected setup")
    assert readiness.value == "MT5-ready"


def test_switching_to_unprepared_tick_mode_blocks_instead_of_reusing_m1(app):
    run_id = app.session_state["active_run"]["id"]
    app.selectbox(key="backtest_mode").select("tick").run(timeout=30)
    assert not app.exception
    assert any("prepared for M1 bar replay" in item.value for item in app.error)
    assert any("python run_all.py --mode tick --tick-root" in item.value for item in app.code)
    assert not app.tabs
    assert not app.metric
    assert app.session_state["active_run"]["id"] == run_id


def test_active_tick_mode_passes_strict_backend_and_disables_bar_only_tools(
        tmp_path, monkeypatch):
    data = tmp_path / "QQQ_TICK_DERIVED_M1.csv"
    root = tmp_path / "wrds_ticks"
    root.mkdir()
    make_synthetic_m1(days=4, seed=11).to_csv(data)
    monkeypatch.setenv("ORB_DATA_FILE", str(data))
    monkeypatch.setenv("ORB_PARAMS_FILE", str(APP_PATH.parent / "configs" / "qqq.json"))
    monkeypatch.setenv("ORB_BACKTEST_MODE", "tick")
    monkeypatch.setenv("ORB_TICK_ROOT", str(root))
    monkeypatch.setenv("ORB_TICK_LATENCY_MS", "2.5")
    monkeypatch.setenv("ORB_TICK_MAX_ENTRY_DELAY_MS", "750")
    monkeypatch.setenv("ORB_TICK_MAX_EOD_DELAY_MS", "900")
    monkeypatch.setenv("ORB_TICK_STRICT", "false")
    calls = []
    progress_callbacks = []
    bar_run_backtest = orb.run_backtest

    def record_execution(bars, params, path=None, execution=None, progress=None):
        calls.append(execution)
        progress_callbacks.append(progress)
        return bar_run_backtest(bars, params, path=path, progress=progress)

    monkeypatch.setattr(orb, "run_backtest", record_execution)
    tick_app = AppTest.from_file(str(APP_PATH)).run(timeout=30)
    assert not tick_app.exception
    assert not calls
    assert any("Full tick replay runs only when requested" in item.value for item in tick_app.info)
    assert tick_app.toggle(key="auto_run").disabled
    assert tick_app.selectbox(key="p_entry_mode").disabled
    assert tick_app.selectbox(key="p_same_bar_rule").disabled
    assert tick_app.number_input(key="p_breakeven_at_r").disabled

    button(tick_app, "Run backtest").click().run(timeout=30)
    assert not tick_app.exception
    assert len(calls) == 1
    assert len(progress_callbacks) == 1 and callable(progress_callbacks[0])
    execution = calls[0]
    assert isinstance(execution, NbboExecution)
    assert execution.store.root == root
    assert execution.store.strategy_offset_hours == 7
    assert execution.config.strict is False
    assert execution.config.latency_ms == 2.5
    assert execution.config.max_entry_delay_ms == 750.0
    assert execution.config.max_eod_delay_ms == 900.0
    assert tick_app.session_state["active_run"]["mode"] == "tick"
    assert any("FULL TICK · WRDS NBBO" in item.value for item in tick_app.markdown)
    assert any("Validation sweeps are disabled" in item.value for item in tick_app.info)
    readiness = next(metric for metric in tick_app.metric if metric.label == "Selected setup")
    assert readiness.value == "Python NBBO only"


def test_tick_mode_with_missing_root_shows_launcher_command(tmp_path, monkeypatch):
    data = tmp_path / "QQQ_TICK_DERIVED_M1.csv"
    make_synthetic_m1(days=2, seed=12).to_csv(data)
    missing = tmp_path / "missing_ticks"
    monkeypatch.setenv("ORB_DATA_FILE", str(data))
    monkeypatch.setenv("ORB_BACKTEST_MODE", "tick")
    monkeypatch.setenv("ORB_TICK_ROOT", str(missing))
    monkeypatch.delenv("ORB_PARAMS_FILE", raising=False)
    tick_app = AppTest.from_file(str(APP_PATH)).run(timeout=30)
    assert not tick_app.exception
    assert any(str(missing) in item.value for item in tick_app.error)
    expected = f'python run_all.py --mode tick --tick-root "{missing}"'
    assert any(expected in item.value for item in tick_app.code)
    assert button(tick_app, "Run backtest").disabled
    assert not tick_app.tabs
