from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from orb.synthetic import make_synthetic_m1


@pytest.fixture
def app(tmp_path, monkeypatch):
    path = tmp_path / "DEMO_M1.csv"
    make_synthetic_m1(days=4, seed=9).to_csv(path)
    monkeypatch.setenv("ORB_DATA_FILE", str(path))
    monkeypatch.delenv("ORB_PARAMS_FILE", raising=False)
    return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_app_load_and_backtest(app):
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ["Overview", "Trades", "Compare", "Diagnostics"]
    assert len(app.metric) == 4
    button(app, "Run backtest").click().run(timeout=30)
    assert not app.exception
    assert len(app.metric) == 4


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
