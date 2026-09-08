from pathlib import Path

from streamlit.testing.v1 import AppTest

from orb.synthetic import make_synthetic_m1


def test_app_load_and_backtest(tmp_path, monkeypatch):
    path = tmp_path / "DEMO_M1.csv"
    make_synthetic_m1(days=4, seed=9).to_csv(path)
    monkeypatch.setenv("ORB_DATA_FILE", str(path))
    monkeypatch.delenv("ORB_PARAMS_FILE", raising=False)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    assert not app.exception
    run = next(button for button in app.button if button.label == "Run backtest")
    run.click().run(timeout=30)
    assert not app.exception
    assert len(app.metric) == 6
