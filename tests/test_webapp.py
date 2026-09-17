"""The browser controls cannot alter broker identity or unseal the holdout."""
from __future__ import annotations

import json
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
