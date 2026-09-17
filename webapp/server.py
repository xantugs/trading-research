"""Serve the research dashboard and run the existing CLI in a background process."""
from __future__ import annotations

import argparse
import csv
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from orb_research.config import Config, load_config


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
CONFIG = ROOT / "configs" / "xauusd_orb_research.json"
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PROGRESS = re.compile(r"^\[[#-]+\]\s+(\d+)%\s+\d+/\d+\s+(.+)$")
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_INSTALL = ("cloudflared is not installed. On Windows: winget install Cloudflare.cloudflared "
                       "(other platforms: developers.cloudflare.com/cloudflare-one/connections/"
                       "connect-networks/downloads). Then rerun with --tunnel.")
EDITABLE = {
    "session_open", "session_exit", "range_minutes", "risk_percent", "reward_risk",
    "stop_method", "stop_width_multiple", "atr_stop_multiple", "breakout_buffer_atr",
    "initial_equity", "spread_multiplier", "slippage_price", "fallback_spread_price",
    "commission_per_lot_side", "commission_rate_pct_side",
}
BOUNDS = {
    "risk_percent": (0.01, 2.0), "reward_risk": (0.1, 10.0),
    "stop_width_multiple": (0.1, 10.0), "atr_stop_multiple": (0.1, 10.0),
    "breakout_buffer_atr": (0.0, 2.0), "initial_equity": (100.0, 1_000_000.0),
    "spread_multiplier": (0.1, 10.0), "slippage_price": (0.0, 10.0),
    "fallback_spread_price": (0.0, 10.0), "commission_per_lot_side": (0.0, 100.0),
    "commission_rate_pct_side": (0.0, 1.0),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_parameters(base: Config, values: object) -> dict:
    """Accept only strategy/cost fields; data paths and holdout cannot be changed in the UI."""
    if not isinstance(values, dict):
        raise ValueError("parameters must be an object")
    extra = set(values) - EDITABLE
    if extra:
        raise ValueError(f"unsupported parameter: {', '.join(sorted(extra))}")
    raw = asdict(base)
    for name, value in values.items():
        if name in ("range_minutes",):
            if type(value) is not int:
                raise ValueError(f"{name} must be an integer")
        elif name in BOUNDS:
            if type(value) not in (int, float):
                raise ValueError(f"{name} must be a number")
            low, high = BOUNDS[name]
            if not low <= value <= high:
                raise ValueError(f"{name} must be between {low:g} and {high:g}")
        elif name in ("session_open", "session_exit"):
            if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
                raise ValueError(f"{name} must be HH:MM")
        elif not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        raw[name] = value
    raw["session_name"] = f"new_york_{raw['session_open'].replace(':', '')}"
    config = Config(**raw)
    config.validate()
    return raw


class ResearchWeb:
    def __init__(self, root: Path = ROOT, config_path: Path = CONFIG):
        self.root = root
        self.base = load_config(config_path)
        self.web_dir = root / "outputs" / "web"
        self.jobs_dir = self.web_dir / "jobs"
        self.runs_dir = root / "outputs" / "runs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active_id: str | None = None
        configured = os.environ.get("ORB_WEB_KEY", "").strip()
        key_file = self.web_dir / "access_key.txt"
        if configured:
            self.key = configured
        elif key_file.exists():
            self.key = key_file.read_text(encoding="utf-8").strip()
        else:
            self.key = secrets.token_urlsafe(32)
            key_file.write_text(self.key + "\n", encoding="utf-8")
        if not self.key:
            raise ValueError("empty web access key")
        self._mark_interrupted()

    def _status_path(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        return self.jobs_dir / run_id / "status.json"

    def _save(self, status: dict) -> None:
        path = self._status_path(status["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _mark_interrupted(self) -> None:
        for path in self.jobs_dir.glob("*/status.json"):
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if status.get("state") in ("queued", "running"):
                status.update(state="interrupted", stage="Worker stopped before completion",
                              finished_at=utc_now())
                self._save(status)

    def _read_status(self, run_id: str) -> dict | None:
        path = self._status_path(run_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def get_run(self, run_id: str) -> dict | None:
        with self.lock:
            status = self._read_status(run_id)
            if status:
                return status
            metrics_path = self.runs_dir / run_id / "metrics.json"
            if not metrics_path.is_file():
                return None
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            params_path = self.runs_dir / run_id / "parameters.json"
            parameters = (json.loads(params_path.read_text(encoding="utf-8"))
                          if params_path.is_file() else {})
            return {
                "id": run_id, "state": "complete", "progress": 100,
                "stage": "Completed before the web dashboard", "created_at": run_id.split("-")[0],
                "parameters": {key: parameters.get(key) for key in EDITABLE if key in parameters},
                "metrics": self._public_metrics(metrics), "recent_log": [], "tick_check": None,
            }

    def drawdown_curve(self, run_id: str) -> list[float]:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        path = self.runs_dir / run_id / "trades.csv"
        if not path.is_file():
            return []
        equity = self.base.initial_equity
        params_path = self.runs_dir / run_id / "parameters.json"
        if params_path.is_file():
            equity = float(json.loads(params_path.read_text(encoding="utf-8")).get(
                "initial_equity", equity))
        peak = equity
        curve = [0.0]
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                equity = float(row["equity_after"])
                peak = max(peak, equity)
                curve.append(round((equity / peak - 1) * 100, 4))
        return curve

    @staticmethod
    def _public_metrics(metrics: dict) -> dict:
        return {key: metrics.get(key) for key in (
            "trades", "expectancy_r", "net_return_pct", "max_drawdown_pct",
            "win_rate", "profit_factor", "ambiguous_m1_exits", "modeled_commission_paid",
        )}

    def history(self) -> list[dict]:
        with self.lock:
            ids = {path.parent.name for path in self.jobs_dir.glob("*/status.json")}
            ids.update(path.parent.name for path in self.runs_dir.glob("*/metrics.json"))
            results = []
            for run_id in ids:
                if not RUN_ID.fullmatch(run_id):
                    continue
                try:
                    record = self.get_run(run_id)
                except (OSError, ValueError):
                    continue
                if record:
                    results.append(record)
            return sorted(results, key=lambda row: row["id"], reverse=True)[:100]

    def submit(self, values: object, tick_check: object) -> dict:
        if type(tick_check) is not bool:
            raise ValueError("tick_check must be true or false")
        parameters = validate_parameters(self.base, values)
        with self.lock:
            if self.active_id:
                raise RuntimeError("A backtest is already running")
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
            job_dir = self.jobs_dir / run_id
            job_dir.mkdir(parents=True)
            config_path = job_dir / "parameters.json"
            config_path.write_text(json.dumps(parameters, indent=2) + "\n", encoding="utf-8")
            status = {
                "id": run_id, "state": "queued", "progress": 0, "stage": "Waiting for worker",
                "created_at": utc_now(), "finished_at": None,
                "parameters": {key: parameters[key] for key in EDITABLE},
                "tick_check": tick_check, "metrics": None, "recent_log": [],
            }
            self._save(status)
            self.active_id = run_id
            threading.Thread(target=self._run_job, args=(run_id, config_path, tick_check),
                             daemon=True, name=f"orb-{run_id}").start()
            return status

    def _run_job(self, run_id: str, config_path: Path, tick_check: bool) -> None:
        output = self.runs_dir / run_id
        argv = [sys.executable, "-u", str(self.root / "run_all.py"), "backtest",
                "--config", str(config_path), "--output-dir", str(output), "--cached-only"]
        if not tick_check:
            argv.append("--skip-ticks")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        log_path = self.jobs_dir / run_id / "worker.log"
        try:
            with self.lock:
                status = self._read_status(run_id)
                status.update(state="running", stage="Preparing cached broker data")
                self._save(status)
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(argv, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                           errors="replace", bufsize=1)
                assert process.stdout is not None
                for line in process.stdout:
                    line = line.rstrip()
                    log.write(line + "\n")
                    log.flush()
                    with self.lock:
                        status = self._read_status(run_id)
                        status["recent_log"] = (status["recent_log"] + [line])[-24:]
                        match = PROGRESS.match(line)
                        if match:
                            status["progress"] = int(match.group(1))
                            status["stage"] = match.group(2)
                        elif line.startswith("Loaded "):
                            status["stage"] = "Running the strategy on M1 bars"
                        elif line.startswith("Signals "):
                            status["stage"] = "Calculating trades and reports"
                        self._save(status)
                code = process.wait()
            with self.lock:
                status = self._read_status(run_id)
                if code == 0:
                    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
                    status.update(state="complete", progress=100, stage="Backtest complete",
                                  metrics=self._public_metrics(metrics))
                else:
                    status.update(state="failed", stage=f"Backtest exited with code {code}")
                status["finished_at"] = utc_now()
                self._save(status)
        except Exception as exc:  # worker errors must remain visible in history
            with self.lock:
                status = self._read_status(run_id)
                status.update(state="failed", stage=f"Worker error: {exc}", finished_at=utc_now())
                self._save(status)
        finally:
            with self.lock:
                if self.active_id == run_id:
                    self.active_id = None


def make_handler(app: ResearchWeb):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ORBWeb/1.0"

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                             "base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, allow_nan=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], app.key):
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "Enter the dashboard access key"})
                return False
            return True

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in static:
                name, content_type = static[path]
                body = (STATIC / name).read_bytes()
                self._headers(HTTPStatus.OK, content_type, len(body))
                self.wfile.write(body)
                return
            if not self._authorized():
                return
            if path == "/api/bootstrap":
                self._json(HTTPStatus.OK, {
                    "parameters": {key: getattr(app.base, key) for key in EDITABLE},
                    "history": app.history(), "active_id": app.active_id,
                    "symbol": app.base.symbol, "server": app.base.expected_server,
                    "start": app.base.start, "development_end": app.base.development_end,
                    "holdout_start": app.base.holdout_start,
                })
            elif path == "/api/runs":
                self._json(HTTPStatus.OK, {"history": app.history(), "active_id": app.active_id})
            elif path.startswith("/api/runs/"):
                run_id = path.removeprefix("/api/runs/")
                if not RUN_ID.fullmatch(run_id):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid run ID"})
                    return
                record = app.get_run(run_id)
                if record:
                    record["drawdown_curve"] = app.drawdown_curve(run_id)
                    self._json(HTTPStatus.OK, record)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/api/runs":
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    raise ValueError("Request body is missing or too large")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) - {"parameters", "tick_check"}:
                    raise ValueError("Expected parameters and tick_check")
                status = app.submit(payload.get("parameters"), payload.get("tick_check", False))
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except RuntimeError as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            self._json(HTTPStatus.ACCEPTED, status)

        def log_message(self, format: str, *args: object) -> None:
            if not self.path.startswith("/api/runs/"):
                super().log_message(format, *args)

    return Handler


def start_tunnel(port: int) -> subprocess.Popen:
    """Open a temporary public HTTPS link to the local dashboard via cloudflared.

    The server itself keeps binding to localhost; cloudflared connects to it from
    this machine and publishes a throwaway *.trycloudflare.com address. The link
    is different every start and closes with the process. Transport is public -
    the access key is what protects the data, same as on localhost.
    """
    binary = shutil.which("cloudflared")
    if not binary:
        raise FileNotFoundError(CLOUDFLARED_INSTALL)
    process = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace")
    threading.Thread(target=_relay_tunnel, args=(process,), daemon=True,
                     name="orb-tunnel").start()
    return process


def _relay_tunnel(process: subprocess.Popen) -> None:
    """Watch cloudflared's log for the public address and announce it once."""
    announced = False
    assert process.stderr is not None
    for line in process.stderr:
        match = TUNNEL_URL.search(line)
        if match and not announced:
            announced = True
            print(f"\nPublic link: {match.group(0)}", flush=True)
            print("Anyone with the link reaches the login page; the access key protects the data.",
                  flush=True)
            print("It changes on every start and stops working when this window closes.\n",
                  flush=True)
        elif not announced and ("ERR" in line or "error" in line.lower()):
            print(f"cloudflared: {line.rstrip()}", file=sys.stderr, flush=True)
    code = process.wait()
    if not announced:
        print(f"cloudflared exited (code {code}) before publishing a link; "
              "the local dashboard is still running.", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8520)
    parser.add_argument("--tunnel", action="store_true",
                        help="Also publish a temporary public HTTPS link with cloudflared, "
                             "for reaching the dashboard from a phone or another machine")
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost"):
        raise ValueError("The research server must bind to localhost; use --tunnel for remote access")
    app = ResearchWeb()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    server.daemon_threads = True
    tunnel: subprocess.Popen | None = None
    try:
        if args.tunnel:
            try:
                tunnel = start_tunnel(args.port)
            except FileNotFoundError as exc:
                print(exc, file=sys.stderr, flush=True)
                return 1
        print(f"Dashboard: http://{args.host}:{args.port}/", flush=True)
        print(f"Access key: {app.key}", flush=True)
        print("The key is also stored in outputs/web/access_key.txt (ignored by Git).", flush=True)
        if tunnel is not None:
            print("Opening the public link (usually a few seconds)...", flush=True)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
