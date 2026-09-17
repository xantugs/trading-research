"""Strict configuration for the first, deliberately small ORB experiment."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    symbol: str = "XAUUSD"
    expected_server: str = "FTMO-Demo"
    source: str = "mt5"  # mt5 | parquet
    data_path: str = ""
    cache_dir: str = "C:/data/ftmo_xauusd_m1"
    tick_cache_dir: str = "C:/data/ftmo_xauusd_ticks"
    output_dir: str = "outputs/xauusd_baseline"
    start: str = "2018-01-01"
    tick_start: str = "2021-10-04"  # first sampled FTMO real-tick day
    development_end: str = "2025-01-01"  # exclusive; 2025 onward stays sealed
    holdout_start: str = "2025-01-01"
    session_name: str = "new_york_0930"
    session_timezone: str = "America/New_York"
    session_open: str = "09:30"
    session_exit: str = "16:00"
    range_minutes: int = 30
    signal_minutes: int = 5
    risk_percent: float = 0.5
    reward_risk: float = 2.0
    stop_method: str = "opposite_range"
    stop_width_multiple: float = 1.0
    atr_stop_multiple: float = 1.0
    breakout_buffer_atr: float = 0.0
    point: float = 0.01
    contract_size: float = 100.0
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    initial_equity: float = 5000.0
    commission_per_lot_side: float = 0.0
    commission_rate_pct_side: float = 0.0
    slippage_price: float = 0.02
    spread_multiplier: float = 1.0
    fallback_spread_price: float = 0.20
    same_bar_rule: str = "sl_first"
    min_trades_for_inference: int = 100
    random_seed: int = 20260917

    def validate(self) -> None:
        known = {"mt5", "parquet"}
        if self.source not in known:
            raise ValueError(f"source must be one of {sorted(known)}")
        if self.source == "parquet" and not self.data_path:
            raise ValueError("data_path is required for parquet source")
        if not self.symbol or not self.expected_server:
            raise ValueError("symbol and expected_server are required")
        if not self.cache_dir or not self.tick_cache_dir or not self.output_dir:
            raise ValueError("cache_dir, tick_cache_dir and output_dir are required")
        start = date.fromisoformat(self.start)
        tick_start = date.fromisoformat(self.tick_start)
        end = date.fromisoformat(self.development_end)
        holdout = date.fromisoformat(self.holdout_start)
        if not start < end <= holdout:
            raise ValueError("start < development_end <= holdout_start is required")
        if not start <= tick_start < holdout:
            raise ValueError("tick_start must lie inside development history")
        ZoneInfo(self.session_timezone)
        for label in (self.session_open, self.session_exit):
            time.fromisoformat(label)
        if self.session_exit <= self.session_open:
            raise ValueError("session_exit must follow session_open on the same local date")
        if self.signal_minutes != 5:
            raise ValueError("the first research version uses M5 signals")
        if self.range_minutes not in (15, 30, 45, 60):
            raise ValueError("range_minutes must be 15, 30, 45 or 60")
        if self.range_minutes % self.signal_minutes:
            raise ValueError("range length must divide into signal bars")
        if self.stop_method not in ("opposite_range", "range_multiple", "atr"):
            raise ValueError("invalid stop_method")
        if self.same_bar_rule != "sl_first":
            raise ValueError("only conservative sl_first is supported for M1 execution")
        for name in ("risk_percent", "reward_risk", "point", "contract_size", "lot_step",
                     "min_lot", "max_lot", "initial_equity", "spread_multiplier"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.risk_percent > 2.0:
            raise ValueError("research risk_percent is capped at 2%")
        for name in ("commission_per_lot_side", "commission_rate_pct_side", "slippage_price", "fallback_spread_price",
                     "breakout_buffer_atr"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.stop_width_multiple <= 0 or self.atr_stop_multiple <= 0:
            raise ValueError("stop multipliers must be positive")
        if self.min_lot > self.max_lot:
            raise ValueError("min_lot exceeds max_lot")


def load_config(path: str | Path) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a JSON object")
    allowed = {field.name for field in fields(Config)}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(unknown)}")
    config = Config(**raw)
    config.validate()
    return config


def serialise(config: Config) -> dict:
    return asdict(config)
