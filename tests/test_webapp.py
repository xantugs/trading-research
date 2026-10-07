"""The browser controls cannot alter broker identity or unseal the holdout."""
from __future__ import annotations

import json
import math
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from orb_research.config import Config
from webapp.server import CONFIG, ResearchWeb, make_handler, validate_parameters


def test_web_parameters_allow_strategy_changes_but_protect_data_and_dates():
    base = Config()
    selected = validate_parameters(base, {
        "range_minutes": 45, "reward_risk": 1.5, "stop_method": "range_multiple",
        "stop_width_multiple": 0.8, "session_open": "08:20",
    })
    assert selected["range_minutes"] == 45
    assert selected["session_name"] == "new_york_0820"
    assert selected["development_end"] == base.development_end
    assert selected["cache_dir"] == base.cache_dir
    with pytest.raises(ValueError, match="unsupported parameter"):
        validate_parameters(base, {"holdout_start": "2026-01-01"})
    with pytest.raises(ValueError, match="unsupported parameter"):
        validate_parameters(base, {"cache_dir": "other-data"})
    with pytest.raises(ValueError, match="HH:MM"):
        validate_parameters(base, {"session_open": "09:30:00"})
    with pytest.raises(ValueError, match="risk_percent"):
        validate_parameters(base, {"risk_percent": 5.0})


def test_history_and_drawdown_use_saved_closed_equity(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    run = tmp_path / "outputs" / "runs" / "20240901T000000Z-abcdef"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(json.dumps({
        "trades": 2, "expectancy_r": -0.1, "net_return_pct": -1.0,
        "max_drawdown_pct": 2.0,
    }), encoding="utf-8")
    (run / "parameters.json").write_text(json.dumps({"initial_equity": 5000,
                                                       "range_minutes": 30}), encoding="utf-8")
    (run / "trades.csv").write_text("equity_after\n5100\n4998\n", encoding="utf-8")
    listed = app.history()
    assert listed[0]["metrics"]["max_drawdown_pct"] == 2.0
    assert app.drawdown_curve(listed[0]["id"]) == [0.0, 0.0, -2.0]
    with pytest.raises(ValueError, match="invalid run ID"):
        app.drawdown_curve("../outside")


def test_trades_are_read_from_the_run_directory_with_display_columns_only(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    run = tmp_path / "outputs" / "runs" / "20240901T000000Z-abcdef"
    run.mkdir(parents=True)
    (run / "trades.csv").write_text(
        "session_date,direction,entry,stop,take_profit,exit,exit_reason,R_result,PnL,"
        "equity_after,MAE,MFE,position_size,ambiguous_m1,commission\n"
        "2024-01-02,long,2050.5,2045.0,2061.5,2061.5,target,2.0,110.0,5110.0,0.1,1.0,0.05,False,1.2\n"
        "2024-01-03,short,2060.0,2065.0,2050.0,2065.0,stop,-1.0,-55.0,5055.0,1.0,0.0,0.05,True,1.2\n",
        encoding="utf-8")
    trades = app.trades("20240901T000000Z-abcdef")
    assert len(trades) == 2
    assert trades[0]["direction"] == "long"
    assert trades[0]["entry"] == 2050.5
    assert trades[0]["ambiguous_m1"] is False
    assert trades[1]["ambiguous_m1"] is True
    assert "commission" not in trades[0], "the table only exposes the display columns"
    assert app.trades("does-not-exist") == []
    with pytest.raises(ValueError, match="invalid run ID"):
        app.trades("../outside")


def test_trades_http_endpoints_serve_json_rows_and_a_raw_csv_download(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    run = tmp_path / "outputs" / "runs" / "20240901T000000Z-abcdef"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(json.dumps({
        "trades": 1, "expectancy_r": 2.0, "net_return_pct": 2.2, "max_drawdown_pct": 0.0,
    }), encoding="utf-8")
    (run / "trades.csv").write_text(
        "session_date,direction,entry,stop,take_profit,exit,exit_reason,R_result,PnL,"
        "equity_after,MAE,MFE,position_size,ambiguous_m1\n"
        "2024-01-02,long,2050.5,2045.0,2061.5,2061.5,target,2.0,110.0,5110.0,0.1,1.0,0.05,False\n",
        encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        request = Request(f"{url}/api/runs/20240901T000000Z-abcdef/trades",
                          headers={"Authorization": f"Bearer {app.key}"})
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
        assert len(payload["trades"]) == 1
        assert payload["trades"][0]["PnL"] == 110.0

        request = Request(f"{url}/api/runs/20240901T000000Z-abcdef/trades.csv",
                          headers={"Authorization": f"Bearer {app.key}"})
        with urlopen(request, timeout=3) as response:
            assert response.headers["Content-Disposition"] == \
                'attachment; filename="20240901T000000Z-abcdef-trades.csv"'
            body = response.read().decode("utf-8")
        assert "2050.5" in body
        assert "session_date" in body.splitlines()[0]

        request = Request(f"{url}/api/runs/missing/trades",
                          headers={"Authorization": f"Bearer {app.key}"})
        with pytest.raises(HTTPError) as missing:
            urlopen(request, timeout=3)
        assert missing.value.code == 404

        with pytest.raises(HTTPError) as unauthorized:
            urlopen(f"{url}/api/runs/20240901T000000Z-abcdef/trades", timeout=3)
        assert unauthorized.value.code == 401
    finally:
        server.shutdown()
        server.server_close()


def test_monthly_stats_fill_trade_free_months_flat_like_report_metrics(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    run = tmp_path / "outputs" / "runs" / "20240901T000000Z-abcdef"
    run.mkdir(parents=True)
    (run / "parameters.json").write_text(json.dumps({"initial_equity": 5000}), encoding="utf-8")
    # Two trades a month apart, then a gap month with no trade at all: the gap month
    # must still count as a (flat, 0%) observation rather than being skipped.
    (run / "trades.csv").write_text(
        "exit_time,equity_after\n"
        "2024-01-15 10:00:00+00:00,5100\n"
        "2024-02-10 10:00:00+00:00,4900\n"
        "2024-04-01 10:00:00+00:00,5150\n",
        encoding="utf-8")
    stats = app.monthly_stats("20240901T000000Z-abcdef")
    assert stats["monthly_volatility_pct"] is not None
    assert stats["sharpe_monthly_annualized"] is not None
    # 4 monthly observations: Jan +2%, Feb ~-3.92%, Mar (no trade) 0%, Apr ~+5.10%.
    returns = [5100 / 5000 - 1, 4900 / 5100 - 1, 0.0, 5150 / 4900 - 1]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    expected_vol = math.sqrt(variance) * 100
    assert stats["monthly_volatility_pct"] == pytest.approx(expected_vol, abs=1e-3)

    assert app.monthly_stats("does-not-exist") == {
        "monthly_volatility_pct": None, "sharpe_monthly_annualized": None,
    }
    with pytest.raises(ValueError, match="invalid run ID"):
        app.monthly_stats("../outside")


def test_get_run_merges_monthly_stats_only_when_asked(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    run = tmp_path / "outputs" / "runs" / "20240901T000000Z-abcdef"
    run.mkdir(parents=True)
    (run / "metrics.json").write_text(json.dumps({
        "trades": 2, "expectancy_r": 0.1, "net_return_pct": 1.0, "max_drawdown_pct": 1.0,
    }), encoding="utf-8")
    (run / "trades.csv").write_text(
        "exit_time,equity_after\n"
        "2024-01-15 10:00:00+00:00,5100\n"
        "2024-03-15 10:00:00+00:00,5250\n",
        encoding="utf-8")
    with_stats = app.get_run("20240901T000000Z-abcdef")
    assert "monthly_volatility_pct" in with_stats["metrics"]
    without_stats = app.get_run("20240901T000000Z-abcdef", monthly_stats=False)
    assert "monthly_volatility_pct" not in without_stats["metrics"]
    # history() must not pay for the trade-ledger read on every polled list refresh.
    assert "monthly_volatility_pct" not in app.history()[0]["metrics"]


def test_http_requires_key_and_rejects_holdout_override(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(url + "/api/bootstrap", timeout=3)
        assert unauthorized.value.code == 401
        request = Request(url + "/api/bootstrap", headers={"Authorization": f"Bearer {app.key}"})
        with urlopen(request, timeout=3) as response:
            assert json.load(response)["holdout_start"] == "2025-01-01"
        body = json.dumps({"parameters": {"holdout_start": "2026-01-01"},
                           "tick_check": False}).encode()
        request = Request(url + "/api/runs", data=body, method="POST",
                          headers={"Authorization": f"Bearer {app.key}",
                                   "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as invalid:
            urlopen(request, timeout=3)
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


# --- job lifecycle -----------------------------------------------------------
# The API surface above is covered; these exercise the worker itself: the
# subprocess, the progress parsing the browser bar depends on, and the states a
# run can end in.

WORKER = '''import argparse, json, sys
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--config", required=True)
parser.add_argument("--output-dir", required=True)
parser.add_argument("--cached-only", action="store_true")
parser.add_argument("--skip-ticks", action="store_true")
args = parser.parse_args()
out = Path(args.output_dir)
out.mkdir(parents=True, exist_ok=True)
out.joinpath("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
out.joinpath("parameters.json").write_text(
    Path(args.config).read_text(encoding="utf-8"), encoding="utf-8")
print("[------------------------------]   0% 0/5 starting", flush=True)
print("Loaded 1,234 M1 bars from 2018 to 2024", flush=True)
print("[############------------------]  40% 2/5 backtest and charts complete", flush=True)
print("Signals 99; trades 42; skipped {}", flush=True)
if __EXIT__:
    print("boom", flush=True)
    raise SystemExit(__EXIT__)
out.joinpath("metrics.json").write_text(json.dumps({
    "trades": 42, "expectancy_r": 0.12, "net_return_pct": 7.5,
    "max_drawdown_pct": 3.25, "win_rate": 0.48, "profit_factor": 1.3,
    "ambiguous_m1_exits": 1, "modeled_commission_paid": 12.5,
    "secret_internal_field": "must not reach the browser",
}), encoding="utf-8")
print("[##############################] 100% 5/5 result bundle complete", flush=True)
'''


def build_app(tmp_path, exit_code=0):
    """A ResearchWeb whose worker is a stand-in for run_all.py backtest."""
    (tmp_path / "run_all.py").write_text(WORKER.replace("__EXIT__", str(exit_code)),
                                         encoding="utf-8")
    return ResearchWeb(root=tmp_path, config_path=CONFIG)


def wait_for(app, run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = app.get_run(run_id)
        if record and record["state"] in ("complete", "failed", "interrupted"):
            return record
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never finished: {app.get_run(run_id)}")


def test_completed_run_reports_progress_metrics_and_releases_the_worker(tmp_path):
    app = build_app(tmp_path)
    accepted = app.submit({"reward_risk": 1.5}, False)
    assert accepted["state"] == "queued" and app.active_id == accepted["id"]

    finished = wait_for(app, accepted["id"])
    assert finished["state"] == "complete"
    assert finished["progress"] == 100
    assert finished["stage"] == "Backtest complete"
    assert finished["metrics"]["trades"] == 42
    assert finished["metrics"]["net_return_pct"] == 7.5
    # Only the whitelisted metrics are published.
    assert "secret_internal_field" not in finished["metrics"]
    # The parsed progress lines and the plain ones both reach the log pane.
    assert any("result bundle complete" in line for line in finished["recent_log"])
    assert app.active_id is None, "worker must be released for the next run"
    assert app.history()[0]["id"] == accepted["id"]


def test_worker_is_asked_for_cached_data_and_ticks_only_when_requested(tmp_path):
    app = build_app(tmp_path)
    plain = wait_for(app, app.submit({}, False)["id"])
    argv = json.loads((tmp_path / "outputs" / "runs" / plain["id"] / "argv.json")
                      .read_text(encoding="utf-8"))
    assert argv[0] == "backtest" and "--cached-only" in argv
    assert "--skip-ticks" in argv, "unchecked tick box must skip the tick comparison"

    checked = wait_for(app, app.submit({}, True)["id"])
    argv = json.loads((tmp_path / "outputs" / "runs" / checked["id"] / "argv.json")
                      .read_text(encoding="utf-8"))
    assert "--skip-ticks" not in argv


def test_only_one_backtest_may_hold_the_local_worker(tmp_path):
    app = build_app(tmp_path)
    first = app.submit({}, False)
    with pytest.raises(RuntimeError, match="already running"):
        app.submit({}, False)
    wait_for(app, first["id"])
    app.submit({}, False)  # the slot is free again


def test_failed_worker_stays_in_history_with_its_exit_code(tmp_path):
    app = build_app(tmp_path, exit_code=3)
    record = wait_for(app, app.submit({}, False)["id"])
    assert record["state"] == "failed"
    assert "exited with code 3" in record["stage"]
    assert record["metrics"] is None
    assert record["finished_at"]
    assert any("boom" in line for line in record["recent_log"])
    assert app.active_id is None


WALK_WORKER = '''import argparse, json, sys
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("command")
parser.add_argument("--config", required=True)
parser.add_argument("--output-dir", required=True)
args = parser.parse_args()
assert args.command == "walk-forward"
out = Path(args.output_dir)
out.mkdir(parents=True, exist_ok=False)
out.joinpath("argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print("Loaded 2,454,505 M1 bars; latest 2024-12-31", flush=True)
print("Fold 1/4: training 2018-2020", flush=True)
print("  train buffer 0.25: 498 trades, mean R +0.0438", flush=True)
print("  selected 0.25 from training only; testing 2021", flush=True)
print("  unseen 2021: 1 trades, mean R +0.5000, return +0.25%", flush=True)
for year in (2022, 2023, 2024):
    print(f"Fold {year-2020}/4: training {year-3}-{year-1}", flush=True)
    print(f"  unseen {year}: 1 trades, mean R +0.5000, return +0.25%", flush=True)
out.joinpath("spec.json").write_text(json.dumps({"base_config": {"initial_equity": 5000},
  "holdout_used": False, "holdout_start": "2025-01-01"}), encoding="utf-8")
out.joinpath("summary.json").write_text(json.dumps({"trades": 4, "mean_r": 0.5,
  "return_pct": 1.0, "max_closed_dd_pct": 0.4, "win_rate": 0.75,
  "profit_factor": 1.2, "secret_internal_field": "private"}), encoding="utf-8")
out.joinpath("folds.csv").write_text(
  "train_start,train_end_inclusive,test_year,selected_buffer,selected_train_mean_r,trades,mean_r,return_pct,max_closed_dd_pct\\n"
  "2018,2020,2021,0.25,0.04,1,0.5,0.25,0.4\\n"
  "2019,2021,2022,0.35,0.05,1,0.5,0.25,0.4\\n"
  "2020,2022,2023,0.45,0.06,1,0.5,0.25,0.4\\n"
  "2021,2023,2024,0.55,0.07,1,0.5,0.25,0.4\\n", encoding="utf-8")
out.joinpath("oos_trades.csv").write_text(
  "equity_after\\n5100\\n5079.6\\n5120\\n5150\\n", encoding="utf-8")
'''


def test_walk_forward_web_run_uses_fixed_plan_and_shows_folds(tmp_path):
    (tmp_path / "run_all.py").write_text(WALK_WORKER, encoding="utf-8")
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    accepted = app.submit_walk_forward()
    assert accepted["kind"] == "walk_forward" and accepted["parameters"] == {}
    finished = wait_for(app, accepted["id"])
    assert finished["state"] == "complete" and finished["progress"] == 100
    assert finished["metrics"]["expectancy_r"] == 0.5
    assert "secret_internal_field" not in finished["metrics"]
    assert [int(fold["test_year"]) for fold in finished["folds"]] == [2021, 2022, 2023, 2024]
    assert app.drawdown_curve(accepted["id"], "walk_forward") == [0.0, 0.0, -0.4, 0.0, 0.0]
    argv = json.loads((tmp_path / "outputs" / "walk_forward" / accepted["id"] / "argv.json")
                      .read_text(encoding="utf-8"))
    assert argv[0] == "walk-forward" and "--config" in argv
    assert "--cached-only" not in argv
    assert app.history()[0]["kind"] == "walk_forward"
    # Existing folders remain selectable after the dashboard restarts.
    restarted = ResearchWeb(root=tmp_path, config_path=CONFIG)
    assert restarted.get_run(accepted["id"])["folds"] == finished["folds"]


def test_walk_forward_endpoint_rejects_parameter_override(tmp_path):
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"parameters": {"holdout_start": "2026-01-01"}}).encode()
        request = Request(f"http://127.0.0.1:{server.server_port}/api/walk-forward",
                          data=body, method="POST",
                          headers={"Authorization": f"Bearer {app.key}",
                                   "Content-Type": "application/json"})
        with pytest.raises(HTTPError) as invalid:
            urlopen(request, timeout=3)
        assert invalid.value.code == 400
        assert "fixed plan" in invalid.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()


def test_a_restart_marks_an_abandoned_run_interrupted(tmp_path):
    app = build_app(tmp_path)
    run_id = app.submit({}, False)["id"]
    wait_for(app, run_id)
    # Rewind that run to look like it was still going when the worker died.
    status_path = tmp_path / "outputs" / "web" / "jobs" / run_id / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update(state="running", progress=40, finished_at=None)
    status_path.write_text(json.dumps(status), encoding="utf-8")

    restarted = ResearchWeb(root=tmp_path, config_path=CONFIG)
    recovered = restarted.get_run(run_id)
    assert recovered["state"] == "interrupted"
    assert recovered["finished_at"], "an interrupted run must not look open forever"
    assert restarted.active_id is None


# --- public link ---------------------------------------------------------------

def test_tunnel_refuses_clearly_when_cloudflared_is_missing(monkeypatch):
    import webapp.server as server

    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="winget install Cloudflare.cloudflared"):
        server.start_tunnel(8520)


def test_tunnel_flag_exits_before_serving_when_cloudflared_is_missing(tmp_path, monkeypatch):
    import webapp.server as server

    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    monkeypatch.setattr(server, "ResearchWeb",
                        lambda: ResearchWeb(root=tmp_path, config_path=CONFIG))
    served = []
    monkeypatch.setattr(server.ThreadingHTTPServer, "serve_forever",
                        lambda self, poll_interval=0.5: served.append(True))
    assert server.main(["--tunnel", "--port", "0"]) == 1
    assert not served, "must not start serving without the link the user asked for"


def test_public_link_is_announced_once_from_cloudflared_output(capsys):
    import webapp.server as server

    class FakeTunnel:
        stderr = iter([
            "2026-09-17T12:00:00Z INF Thank you for trying Cloudflare Tunnel.\n",
            "2026-09-17T12:00:01Z INF +----------------------------------------------+\n",
            "2026-09-17T12:00:01Z INF |  https://quiet-otter-1234.trycloudflare.com  |\n",
            "2026-09-17T12:00:01Z INF +----------------------------------------------+\n",
            "2026-09-17T12:00:02Z INF Registered tunnel connection https://quiet-otter-1234.trycloudflare.com\n",
        ])

        @staticmethod
        def wait():
            return 0

    server._relay_tunnel(FakeTunnel())
    out = capsys.readouterr().out
    assert out.count("https://quiet-otter-1234.trycloudflare.com") == 1
    assert "access key protects the data" in out


def test_relay_reports_a_tunnel_that_dies_without_a_link(capsys):
    import webapp.server as server

    class DeadTunnel:
        stderr = iter(["2026-09-17T12:00:00Z ERR failed to connect to the edge\n"])

        @staticmethod
        def wait():
            return 1

    server._relay_tunnel(DeadTunnel())
    err = capsys.readouterr().err
    assert "failed to connect" in err
    assert "exited (code 1) before publishing a link" in err
    assert "local dashboard is still running" in err


def test_trade_chart_reads_the_trade_window_with_the_runs_own_clock(tmp_path):
    from dataclasses import asdict, replace

    import pandas as pd

    config = replace(Config(), source="mt5", cache_dir=str(tmp_path / "cache"), expected_server="Demo",
                     symbol="IDX", broker_clock="new_york_plus_7", signal_minutes=15)
    month = tmp_path / "cache" / "Demo" / "IDX"
    month.mkdir(parents=True)
    # 09:00-12:00 New York on 2023-01-03, stored in the server clock (New York + 7 h)
    index = pd.date_range("2023-01-03 16:00", "2023-01-03 19:00", freq="min", tz="UTC", inclusive="left")
    pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "tick_volume": 1,
                  "spread": 0, "real_volume": 0}, index=index).to_parquet(month / "202301.parquet")
    run = tmp_path / "outputs" / "runs" / "20240901T000000Z-abcdef"
    run.mkdir(parents=True)
    (run / "config_used.json").write_text(json.dumps(asdict(config)), encoding="utf-8")
    (run / "trades.csv").write_text(
        "timestamp,session_date,direction,range_start,range_end,range_high,range_low,entry,stop,"
        "take_profit,exit_time,exit,exit_reason,R_result,PnL\n"
        "2023-01-03 15:00:00+00:00,2023-01-03,long,2023-01-03 14:30:00+00:00,2023-01-03 14:45:00+00:00,"
        "101,99,101,99,104,2023-01-03 16:00:00+00:00,104,target,1.5,10\n", encoding="utf-8")
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    chart = app.trade_chart("20240901T000000Z-abcdef", 0)
    assert chart["timeframe_minutes"] == 15
    # 30 minutes before the range (09:00 NY) to 30 minutes after the exit (11:30 NY)
    assert [c[0] for c in chart["candles"]][:2] == ["2023-01-03T09:00:00-05:00", "2023-01-03T09:15:00-05:00"]
    assert len(chart["candles"]) == 10
    assert chart["trade"]["entry_time"] == "2023-01-03T10:00:00-05:00"
    assert chart["trade"]["take_profit"] == 104.0
    assert app.trade_chart("20240901T000000Z-abcdef", 1) is None
    with pytest.raises(ValueError, match="invalid run ID"):
        app.trade_chart("../x", 0)
