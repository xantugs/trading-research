"""Serve the research dashboard and run the existing CLI in a background process."""
from __future__ import annotations

import argparse
import csv
import hmac
import json
import math
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import threading
from dataclasses import asdict, replace
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pandas as pd

from orb_research.config import Config, load_config
from orb_research.data_loader import decode_broker_clock
from orb_research.tester_report import tester_report


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
CONFIG = ROOT / "configs" / "indices" / "ftmo_us100_orb_framework_15_15.json"
# Sidebar presets: each brings its own data window and broker clock, then every editable
# field stays tunable.  The first entry whose file is the app's base config is the default.
PRESETS = (
    {"id": "us100_candidate", "label": "US100 candidate · forward test (0.5%)",
     "summary": "The frozen candidate (docs/US100_CANDIDATE_PLAN.md): FTMO US100.cash, 09:30 NY "
                "15m range, first 15m close outside it per direction before 12:00, held to stop "
                "or 1.5R, at the forward test's 0.5% risk with the EA's account protection. "
                "Passed all robustness checks; 2026 negative.",
     "config": "configs/indices/ftmo_us100_orb_framework_15_15.json",
     "overrides": {"risk_percent": 0.5, "risk_throttle_below_pct": 5.0, "risk_throttle_percent": 0.25, "daily_loss_stop_pct": 2.5, "equity_stop_pct": 8.5}},
    {"id": "us100_candidate_1p5", "label": "US100 candidate · 1.5% risk",
     "summary": "The same frozen rules at 1.5% risk, the forward test's second account: about a "
                "coin flip to pass an FTMO challenge, roughly two months when it does.",
     "config": "configs/indices/ftmo_us100_orb_framework_15_15.json",
     "overrides": {"risk_percent": 1.5, "risk_throttle_below_pct": 5.0, "risk_throttle_percent": 0.25, "daily_loss_stop_pct": 2.5, "equity_stop_pct": 8.5}},
    {"id": "orb_framework", "label": "Video EA · 15m range, 5m entries",
     "summary": "ORB-framework EA as shown: 09:30 NY range, first 5m close outside it per "
                "direction, no entries from 12:00, held to stop or 1.5R, 2% risk.",
     "config": "configs/orb_framework.json"},
    {"id": "orb_framework_15", "label": "Video EA · 15m range, 15m entries",
     "summary": "The same EA with the original rules: range and entry both on 15 minutes.",
     "config": "configs/orb_framework.json", "overrides": {"signal_minutes": 15}},
    {"id": "trading_steady_bot", "label": "Live bot · ATR filter, long only",
     "summary": "His Python bot: 15m/15m, long only, range 1.25–3× ATR(1344), re-entry after a "
                "close before 12:00, 1.5R, 1.5% risk, 7% a year financing.",
     "config": "configs/trading_steady_bot.json"},
    {"id": "trading_steady_trailing", "label": "Live bot · ATR trailing stop",
     "summary": "The live bot with no fixed target: from 1.5R the stop trails 1 × ATR(14).",
     "config": "configs/trading_steady_trailing.json"},
    {"id": "trading_steady_stop_entry", "label": "Live bot · stop-order entry, 2R",
     "summary": "Enter on the first touch of the range edge instead of a close, target 2R, "
                "one trade a day.",
     "config": "configs/trading_steady_stop_entry.json"},
    {"id": "trading_steady_end_of_day", "label": "Live bot · flat at 16:00",
     "summary": "The live bot, but every position is closed at 16:00 New York.",
     "config": "configs/trading_steady_end_of_day.json"},
    {"id": "legacy_research", "label": "Original research baseline (legacy clock)",
     "summary": "The September 2026 baseline for reproducing old runs. Its clock reads the "
                "cache as UTC, so '09:30' is not the New York open.",
     "config": "configs/xauusd_orb_research.json"},
)
WALK_FORWARD_CONFIG = ROOT / "configs" / "walk_forward_base.json"
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
TRADES_ROUTE = re.compile(r"^/api/runs/([A-Za-z0-9_-]{1,80})/trades$")
TRADES_CSV_ROUTE = re.compile(r"^/api/runs/([A-Za-z0-9_-]{1,80})/trades\.csv$")
REPORT_ROUTE = re.compile(r"^/api/runs/([A-Za-z0-9_-]{1,80})/report$")
TRADE_CHART_ROUTE = re.compile(r"^/api/runs/([A-Za-z0-9_-]{1,80})/trades/(\d{1,6})/chart$")
# A trade chart uses the run's entry timeframe, or the next coarser one that keeps it readable.
CHART_TIMEFRAMES = (1, 5, 15, 30, 60, 120, 240, 1440)
CHART_MAX_BARS = 240
PROGRESS = re.compile(r"^\[[#-]+\]\s+(\d+)%\s+\d+/\d+\s+(.+)$")
FOLD_START = re.compile(r"^Fold (\d)/4: training (\d{4})-(\d{4})$")
FOLD_TRAIN = re.compile(r"^  train buffer (\d+\.\d+):")
FOLD_TEST = re.compile(r"^  unseen (\d{4}):")
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_INSTALL = ("cloudflared is not installed. On Windows: winget install Cloudflare.cloudflared "
                       "(other platforms: developers.cloudflare.com/cloudflare-one/connections/"
                       "connect-networks/downloads). Then rerun with --tunnel.")
EDITABLE = {
    "session_open", "session_exit", "range_minutes", "signal_minutes", "trade_direction",
    "position_exit", "trades_per_session", "risk_percent", "reward_risk",
    "stop_method", "stop_width_multiple", "atr_stop_multiple", "breakout_buffer_atr",
    "initial_equity", "spread_multiplier", "slippage_price", "fallback_spread_price",
    "commission_per_lot_side", "commission_rate_pct_side",
    "entry_mode", "or_candle", "or_atr_period", "or_min_atr", "or_max_atr", "flat_time",
    "trail_atr_multiple", "trail_activation_r", "trail_atr_period", "financing_pct_annual",
    "risk_throttle_below_pct", "risk_throttle_percent", "daily_loss_stop_pct", "equity_stop_pct",
}
# Integer fields and their allowed range; range/entry minutes are checked by Config itself.
INTEGERS = {"range_minutes": None, "signal_minutes": None,
            "or_atr_period": (1, 20_000), "trail_atr_period": (1, 1_000)}
# Fields where null switches the rule off (a blank box in the browser).
NULLABLE = {"or_min_atr", "or_max_atr", "trail_atr_multiple", "flat_time", "risk_throttle_below_pct",
            "risk_throttle_percent", "daily_loss_stop_pct", "equity_stop_pct"}
BOUNDS = {
    "risk_percent": (0.01, 2.0), "reward_risk": (0.1, 10.0),
    "stop_width_multiple": (0.1, 10.0), "atr_stop_multiple": (0.1, 10.0),
    "breakout_buffer_atr": (0.0, 2.0), "initial_equity": (100.0, 1_000_000.0),
    "spread_multiplier": (0.1, 10.0), "slippage_price": (0.0, 10.0),
    "fallback_spread_price": (0.0, 10.0), "commission_per_lot_side": (0.0, 100.0),
    "commission_rate_pct_side": (0.0, 1.0),
    "or_min_atr": (0.01, 20.0), "or_max_atr": (0.01, 20.0), "trail_atr_multiple": (0.1, 10.0),
    "trail_activation_r": (0.0, 10.0), "financing_pct_annual": (0.0, 50.0),
    "risk_throttle_below_pct": (0.1, 50.0), "risk_throttle_percent": (0.01, 2.0),
    "daily_loss_stop_pct": (0.1, 50.0), "equity_stop_pct": (0.1, 50.0),
}
# What a tick-scalper run folder reports about its settings (it has no ORB form fields).
SCALPER_KEYS = ("range_open", "range_minutes", "cutoff", "sl_points", "tp_points", "trail_points",
                "trail_start_points", "execution_delay_ms", "one_cancels_other", "risk_percent",
                "initial_equity", "trade_direction")
# Columns shown in the browser's trade table; the full record (and any other
# column) remains available through the raw trades.csv / oos_trades.csv download.
TRADE_COLUMNS = ("session_date", "direction", "entry", "stop", "take_profit", "exit",
                 "exit_reason", "R_result", "PnL", "equity_after", "MAE", "MFE",
                 "position_size", "ambiguous_m1", "financing")
NUMERIC_TRADE_FIELDS = {"entry", "stop", "take_profit", "exit", "R_result", "PnL",
                        "equity_after", "MAE", "MFE", "position_size", "financing"}


def _finite(value):
    """JSON-safe copy: non-finite floats (an undefined ratio) become null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite(item) for item in value]
    return value


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
        if value is None and name in NULLABLE:
            pass
        elif name in INTEGERS:
            if type(value) is not int:
                raise ValueError(f"{name} must be an integer")
            if INTEGERS[name] and not INTEGERS[name][0] <= value <= INTEGERS[name][1]:
                raise ValueError(f"{name} must be between {INTEGERS[name][0]} and {INTEGERS[name][1]}")
        elif name in BOUNDS:
            if type(value) not in (int, float):
                raise ValueError(f"{name} must be a number")
            low, high = BOUNDS[name]
            if not low <= value <= high:
                raise ValueError(f"{name} must be between {low:g} and {high:g}")
        elif name in ("session_open", "session_exit", "flat_time"):
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
        self.presets: dict[str, tuple[dict, Config]] = {}
        self.default_preset = None
        for spec in PRESETS:
            path = ROOT / spec["config"]
            config = replace(load_config(path), **spec.get("overrides", {}))
            config.validate()
            self.presets[spec["id"]] = (spec, config)
            if self.default_preset is None and path.resolve() == Path(config_path).resolve():
                self.default_preset = spec["id"]
        if self.default_preset is None:
            spec = {"id": "configured", "label": "Configured base",
                    "summary": f"The dashboard's base configuration ({Path(config_path).name}).",
                    "config": str(config_path)}
            self.presets = {"configured": (spec, self.base), **self.presets}
            self.default_preset = "configured"
        self._reports: dict[str, dict] = {}
        self.walk_forward_config = WALK_FORWARD_CONFIG
        self.web_dir = root / "outputs" / "web"
        self.jobs_dir = self.web_dir / "jobs"
        self.runs_dir = root / "outputs" / "runs"
        self.walk_forward_dir = root / "outputs" / "walk_forward"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.walk_forward_dir.mkdir(parents=True, exist_ok=True)
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

    def get_run(self, run_id: str, *, monthly_stats: bool = True) -> dict | None:
        with self.lock:
            status = self._read_status(run_id)
            if status:
                record = status
            else:
                summary_path = self.walk_forward_dir / run_id / "summary.json"
                if summary_path.is_file():
                    metrics, folds = self._walk_forward_result(self.walk_forward_dir / run_id)
                    record = {
                        "id": run_id, "kind": "walk_forward", "state": "complete", "progress": 100,
                        "stage": "Completed before the web dashboard", "created_at": run_id.split("-")[0],
                        "parameters": {}, "metrics": metrics, "folds": folds,
                        "recent_log": [], "tick_check": False,
                    }
                else:
                    metrics_path = self.runs_dir / run_id / "metrics.json"
                    if not metrics_path.is_file():
                        return None
                    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                    params_path = self.runs_dir / run_id / "parameters.json"
                    parameters = (json.loads(params_path.read_text(encoding="utf-8"))
                                  if params_path.is_file() else {})
                    record = {
                        "id": run_id, "kind": "backtest", "state": "complete", "progress": 100,
                        "stage": "Completed before the web dashboard", "created_at": run_id.split("-")[0],
                        "parameters": ({key: parameters.get(key) for key in SCALPER_KEYS if key in parameters}
                                       if parameters.get("strategy") == "tick_scalper" else
                                       {key: parameters.get(key) for key in EDITABLE if key in parameters}),
                        "strategy": parameters.get("strategy", "orb"),
                        "metrics": self._public_metrics(metrics), "recent_log": [], "tick_check": None,
                    }
            # Computed fresh from the trade ledger rather than cached, so every run - however
            # its status was produced - reports volatility and Sharpe the same way.
            if monthly_stats and record.get("state") == "complete" and record.get("metrics") is not None:
                record = {**record, "metrics": {**record["metrics"],
                                                **self.monthly_stats(run_id, record.get("kind", "backtest"))}}
            return record

    def trades_csv_path(self, run_id: str, kind: str) -> Path:
        if kind == "walk_forward":
            return self.walk_forward_dir / run_id / "oos_trades.csv"
        if kind == "backtest":
            return self.runs_dir / run_id / "trades.csv"
        raise ValueError("invalid run kind")

    def _resolve_equity(self, run_id: str, kind: str) -> float | None:
        """Starting equity for a run, or None when it cannot be priced yet (e.g. no spec.json)."""
        if kind == "walk_forward":
            spec_path = self.walk_forward_dir / run_id / "spec.json"
            if not spec_path.is_file():
                return None
            return float(json.loads(spec_path.read_text(encoding="utf-8"))[
                "base_config"]["initial_equity"])
        equity = self.base.initial_equity
        params_path = self.runs_dir / run_id / "parameters.json"
        if params_path.is_file():
            equity = float(json.loads(params_path.read_text(encoding="utf-8")).get(
                "initial_equity", equity))
        return equity

    def drawdown_curve(self, run_id: str, kind: str = "backtest") -> list[float]:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        path = self.trades_csv_path(run_id, kind)
        equity = self._resolve_equity(run_id, kind)
        if not path.is_file() or equity is None:
            return []
        peak = equity
        curve = [0.0]
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                equity = float(row["equity_after"])
                peak = max(peak, equity)
                curve.append(round((equity / peak - 1) * 100, 4))
        return curve

    def monthly_stats(self, run_id: str, kind: str = "backtest") -> dict:
        """Monthly-return volatility and annualized Sharpe from the run's own trade ledger.

        Mirrors orb_research.report.metrics(): a calendar month with no closed trade
        carries the prior month's equity flat (a 0% month) rather than being skipped, so
        every run - single backtest or walk-forward - is scored the same way and the
        figures stay comparable across kinds.
        """
        none_result = {"monthly_volatility_pct": None, "sharpe_monthly_annualized": None}
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        path = self.trades_csv_path(run_id, kind)
        equity = self._resolve_equity(run_id, kind)
        if not path.is_file() or equity is None:
            return none_result
        last_equity_by_month: dict[tuple[int, int], float] = {}
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            if "exit_time" not in fields or "equity_after" not in fields:
                return none_result
            for row in reader:
                if not row["exit_time"] or not row["equity_after"]:
                    continue
                key = (int(row["exit_time"][:4]), int(row["exit_time"][5:7]))
                last_equity_by_month[key] = float(row["equity_after"])
        if not last_equity_by_month:
            return none_result
        month, last_month = min(last_equity_by_month), max(last_equity_by_month)
        returns = []
        while True:
            equity_end = last_equity_by_month.get(month, equity)
            returns.append(equity_end / equity - 1)
            equity = equity_end
            if month == last_month:
                break
            month = (month[0] + 1, 1) if month[1] == 12 else (month[0], month[1] + 1)
        if len(returns) < 2:
            return none_result
        mean = statistics.fmean(returns)
        stdev = statistics.stdev(returns)
        sharpe = mean / stdev * math.sqrt(12) if len(returns) >= 3 and stdev > 0 else None
        return {"monthly_volatility_pct": round(stdev * 100, 4),
               "sharpe_monthly_annualized": round(sharpe, 4) if sharpe is not None else None}

    def trades(self, run_id: str, kind: str = "backtest") -> list[dict]:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        path = self.trades_csv_path(run_id, kind)
        if not path.is_file():
            return []
        rows = []
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                record = {}
                for name in TRADE_COLUMNS:
                    value = row.get(name, "")
                    if name in NUMERIC_TRADE_FIELDS:
                        record[name] = float(value) if value else None
                    elif name == "ambiguous_m1":
                        record[name] = value.strip().lower() == "true"
                    else:
                        record[name] = value
                rows.append(record)
        return rows

    def preset_list(self) -> list[dict]:
        return [{"id": spec["id"], "label": spec["label"], "summary": spec["summary"],
                 "parameters": {key: getattr(config, key) for key in EDITABLE},
                 "window": {"symbol": config.symbol, "server": config.expected_server,
                            "start": config.start, "development_end": config.development_end,
                            "holdout_start": config.holdout_start,
                            "holdout_opened": config.holdout_opened,
                            "broker_clock": config.broker_clock}}
                for spec, config in self.presets.values()]

    def report(self, run_id: str, kind: str = "backtest") -> dict | None:
        """MT5-style tester report for a completed run; run folders never change, so cache it."""
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        if run_id in self._reports:
            return self._reports[run_id]
        path = self.trades_csv_path(run_id, kind)
        equity = self._resolve_equity(run_id, kind)
        if not path.is_file() or equity is None:
            return None
        timezone_name = "America/New_York"
        params_path = (self.runs_dir if kind == "backtest" else self.walk_forward_dir) / run_id / "parameters.json"
        if params_path.is_file():
            timezone_name = json.loads(params_path.read_text(encoding="utf-8")).get(
                "session_timezone", timezone_name)
        trades = pd.read_csv(path)
        result = _finite(tester_report(trades, equity, timezone_name))
        self._reports[run_id] = result
        return result

    def trade_chart(self, run_id: str, index: int) -> dict | None:
        """Candles around one trade of a completed backtest, with its range, stop and target.

        Only the cached months that the trade spans are read, decoded with the run's own
        broker clock, so the chart shows exactly the bars the engine traded on.
        """
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid run ID")
        run_dir = self.runs_dir / run_id
        trades_path, config_path = run_dir / "trades.csv", run_dir / "config_used.json"
        if not trades_path.is_file() or not config_path.is_file():
            return None
        trades = pd.read_csv(trades_path)
        if not 0 <= index < len(trades):
            return None
        trade = trades.iloc[index]
        config = load_config(config_path)
        if config.source != "mt5":
            raise ValueError("trade charts need the MT5 M1 cache")
        zone = config.session_timezone
        start = pd.Timestamp(trade.range_start).tz_convert("UTC") - pd.Timedelta(minutes=30)
        end = pd.Timestamp(trade.exit_time).tz_convert("UTC") + pd.Timedelta(minutes=30)
        cache = Path(config.cache_dir) / config.expected_server / config.symbol
        months = pd.period_range((start - pd.Timedelta(days=2)).tz_convert(None),
                                 (end + pd.Timedelta(days=2)).tz_convert(None), freq="M")
        frames = [pd.read_parquet(path) for month in months
                  if (path := cache / month.strftime("%Y%m.parquet")).is_file()]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            raise ValueError("the cached M1 months for this trade are missing")
        m1 = pd.concat(frames).sort_index()
        m1 = decode_broker_clock(m1.loc[~m1.index.duplicated(keep="first")], config)
        m1 = m1.loc[(m1.index >= start) & (m1.index < end)]
        minutes = next((m for m in CHART_TIMEFRAMES
                        if m >= config.signal_minutes and len(m1) / m <= CHART_MAX_BARS), 1440)
        bars = m1.resample(f"{minutes}min", label="left", closed="left", origin="epoch").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

        def when(value):
            return None if pd.isna(value) else pd.Timestamp(value).tz_convert(zone).isoformat()

        def number(name):
            value = trade.get(name)
            return None if value is None or pd.isna(value) else float(value)

        return {
            "timezone": zone, "timeframe_minutes": minutes, "symbol": config.symbol,
            "candles": [[when(t), float(row.open), float(row.high), float(row.low), float(row.close)]
                        for t, row in bars.iterrows()],
            "trade": {
                "index": index, "session_date": str(trade.session_date),
                "direction": str(trade.direction), "exit_reason": str(trade.exit_reason),
                "entry_time": when(trade.timestamp), "exit_time": when(trade.exit_time),
                "range_start": when(trade.range_start), "range_end": when(trade.range_end),
                **{name: number(name) for name in ("range_high", "range_low", "entry", "stop",
                                                     "take_profit", "exit", "R_result", "PnL")},
            },
        }

    @staticmethod
    def _public_metrics(metrics: dict) -> dict:
        return {key: metrics.get(key) for key in (
            "trades", "expectancy_r", "net_return_pct", "max_drawdown_pct",
            "win_rate", "profit_factor", "ambiguous_m1_exits", "modeled_commission_paid",
        )}

    @staticmethod
    def _walk_forward_result(output: Path) -> tuple[dict, list[dict]]:
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        metrics = {
            "trades": summary["trades"], "expectancy_r": summary["mean_r"],
            "net_return_pct": summary["return_pct"],
            "max_drawdown_pct": summary["max_closed_dd_pct"],
            "win_rate": summary["win_rate"], "profit_factor": summary["profit_factor"],
        }
        columns = ("train_start", "train_end_inclusive", "test_year", "selected_buffer",
                   "selected_train_mean_r", "trades", "mean_r", "return_pct",
                   "max_closed_dd_pct")
        folds = []
        with (output / "folds.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                folds.append({name: float(row[name]) if row[name] else None for name in columns})
        return metrics, folds

    def history(self) -> list[dict]:
        with self.lock:
            ids = {path.parent.name for path in self.jobs_dir.glob("*/status.json")}
            ids.update(path.parent.name for path in self.runs_dir.glob("*/metrics.json"))
            ids.update(path.parent.name for path in self.walk_forward_dir.glob("*/summary.json"))
            results = []
            for run_id in ids:
                if not RUN_ID.fullmatch(run_id):
                    continue
                try:
                    # The list view skips the per-run trade-ledger read that powers
                    # volatility/Sharpe; the detail endpoint below computes those on demand.
                    record = self.get_run(run_id, monthly_stats=False)
                except (OSError, ValueError):
                    continue
                if record:
                    results.append(record)
            return sorted(results, key=lambda row: row["id"], reverse=True)[:100]

    def submit(self, values: object, tick_check: object, preset: object = None) -> dict:
        if type(tick_check) is not bool:
            raise ValueError("tick_check must be true or false")
        preset = self.default_preset if preset is None else preset
        if not isinstance(preset, str) or preset not in self.presets:
            raise ValueError("unknown preset")
        parameters = validate_parameters(self.presets[preset][1], values)
        with self.lock:
            if self.active_id:
                raise RuntimeError("A research run is already running")
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
            job_dir = self.jobs_dir / run_id
            job_dir.mkdir(parents=True)
            config_path = job_dir / "parameters.json"
            config_path.write_text(json.dumps(parameters, indent=2) + "\n", encoding="utf-8")
            status = {
                "id": run_id, "kind": "backtest", "state": "queued", "progress": 0,
                "stage": "Waiting for worker",
                "created_at": utc_now(), "finished_at": None,
                "parameters": {key: parameters[key] for key in EDITABLE},
                "preset": preset, "window": {"start": parameters["start"],
                                             "development_end": parameters["development_end"],
                                             "broker_clock": parameters["broker_clock"]},
                "tick_check": tick_check, "metrics": None, "recent_log": [],
            }
            self._save(status)
            self.active_id = run_id
            threading.Thread(target=self._run_job, args=(run_id, config_path, tick_check),
                             daemon=True, name=f"orb-{run_id}").start()
            return status

    def submit_walk_forward(self) -> dict:
        with self.lock:
            if self.active_id:
                raise RuntimeError("A research run is already running")
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
            status = {
                "id": run_id, "kind": "walk_forward", "state": "queued", "progress": 0,
                "stage": "Waiting for worker", "created_at": utc_now(), "finished_at": None,
                "parameters": {}, "tick_check": False, "metrics": None, "folds": [],
                "recent_log": [],
            }
            self._save(status)
            self.active_id = run_id
            threading.Thread(target=self._run_walk_forward_job, args=(run_id,),
                             daemon=True, name=f"orb-walk-{run_id}").start()
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

    def _run_walk_forward_job(self, run_id: str) -> None:
        output = self.walk_forward_dir / run_id
        argv = [sys.executable, "-u", str(self.root / "run_all.py"), "walk-forward",
                "--config", str(self.walk_forward_config), "--output-dir", str(output)]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        log_path = self.jobs_dir / run_id / "worker.log"
        completed_steps = 0
        try:
            with self.lock:
                status = self._read_status(run_id)
                status.update(state="running", stage="Loading cached 2018–2024 M1 data")
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
                        if line.startswith("Loaded "):
                            status.update(progress=5, stage="Development data ready")
                        elif match := FOLD_START.match(line):
                            status["stage"] = f"Fold {match.group(1)}/4: training {match.group(2)}–{match.group(3)}"
                        elif match := FOLD_TRAIN.match(line):
                            completed_steps += 1
                            status.update(progress=min(95, 5 + int(90 * completed_steps / 20)),
                                          stage=f"Evaluated training buffer {match.group(1)}")
                        elif FOLD_TEST.match(line):
                            completed_steps += 1
                            status.update(progress=min(95, 5 + int(90 * completed_steps / 20)),
                                          stage=line.strip())
                        elif line.strip().startswith("selected "):
                            status["stage"] = line.strip()
                        self._save(status)
                code = process.wait()
            with self.lock:
                status = self._read_status(run_id)
                if code == 0:
                    metrics, folds = self._walk_forward_result(output)
                    status.update(state="complete", progress=100,
                                  stage="Walk-forward complete", metrics=metrics, folds=folds)
                else:
                    status.update(state="failed", stage=f"Walk-forward exited with code {code}")
                status["finished_at"] = utc_now()
                self._save(status)
        except Exception as exc:
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

        def _headers(self, status: int, content_type: str, length: int,
                    extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                             "base-uri 'none'; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
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
                    "presets": app.preset_list(), "default_preset": app.default_preset,
                    "history": app.history(), "active_id": app.active_id,
                    "symbol": app.base.symbol, "server": app.base.expected_server,
                    "start": app.base.start, "development_end": app.base.development_end,
                    "holdout_start": app.base.holdout_start,
                })
            elif path == "/api/runs":
                self._json(HTTPStatus.OK, {"history": app.history(), "active_id": app.active_id})
            elif (match := TRADES_CSV_ROUTE.match(path)):
                run_id = match.group(1)
                record = app.get_run(run_id, monthly_stats=False)
                if not record:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found"})
                    return
                csv_path = app.trades_csv_path(run_id, record.get("kind", "backtest"))
                if not csv_path.is_file():
                    self._json(HTTPStatus.NOT_FOUND, {"error": "No trades for this run"})
                    return
                body = csv_path.read_bytes()
                self._headers(HTTPStatus.OK, "text/csv; charset=utf-8", len(body),
                              {"Content-Disposition": f'attachment; filename="{run_id}-trades.csv"'})
                self.wfile.write(body)
            elif (match := REPORT_ROUTE.match(path)):
                run_id = match.group(1)
                record = app.get_run(run_id, monthly_stats=False)
                if not record or record.get("state") != "complete":
                    self._json(HTTPStatus.NOT_FOUND, {"error": "No report for this run"})
                    return
                try:
                    report = app.report(run_id, record.get("kind", "backtest"))
                except (OSError, ValueError, KeyError) as exc:  # a malformed ledger, not a crash
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Report failed: {exc}"})
                    return
                if report is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "No trades for this run"})
                    return
                self._json(HTTPStatus.OK, report)
            elif (match := TRADE_CHART_ROUTE.match(path)):
                try:
                    chart = app.trade_chart(match.group(1), int(match.group(2)))
                except (OSError, ValueError, KeyError) as exc:
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Chart failed: {exc}"})
                    return
                if chart is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "No such trade"})
                    return
                self._json(HTTPStatus.OK, chart)
            elif (match := TRADES_ROUTE.match(path)):
                run_id = match.group(1)
                record = app.get_run(run_id, monthly_stats=False)
                if not record:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found"})
                    return
                self._json(HTTPStatus.OK,
                          {"trades": app.trades(run_id, record.get("kind", "backtest"))})
            elif path.startswith("/api/runs/"):
                run_id = path.removeprefix("/api/runs/")
                if not RUN_ID.fullmatch(run_id):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid run ID"})
                    return
                record = app.get_run(run_id)
                if record:
                    record["drawdown_curve"] = app.drawdown_curve(
                        run_id, record.get("kind", "backtest"))
                    self._json(HTTPStatus.OK, record)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in ("/api/runs", "/api/walk-forward"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            try:
                if path == "/api/walk-forward":
                    length = int(self.headers.get("Content-Length", "0"))
                    if length != 0:
                        raise ValueError("Walk-forward uses the fixed plan; no parameters accepted")
                    status = app.submit_walk_forward()
                else:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16_384:
                        raise ValueError("Request body is missing or too large")
                    payload = json.loads(self.rfile.read(length))
                    if (not isinstance(payload, dict)
                            or set(payload) - {"parameters", "tick_check", "preset"}):
                        raise ValueError("Expected parameters, tick_check and preset")
                    status = app.submit(payload.get("parameters"), payload.get("tick_check", False),
                                        payload.get("preset"))
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
