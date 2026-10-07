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
    # The date the 2025+ holdout was deliberately opened (6 October 2026, at the project owner's
    # request).  Only then may development_end pass holdout_start; holdout_start stays the
    # historical boundary, and results that use later data are no longer out-of-sample.
    holdout_opened: str | None = None
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
    # Signal-time entry rules; null disables each one.
    entry_cutoff_minutes_after_or: float | None = None  # skip a first breakout that closes later
    min_or_width_atr: float | None = None  # skip sessions whose range is narrower than this × ATR14
    # ORB-framework EA mechanics (defaults reproduce the original engine exactly).
    trade_direction: str = "both"  # both | long | short; the other side's breakouts are ignored
    # "session_exit": flat at session_exit. "stop_or_target": session_exit only ends new entries and
    # every position runs, across days if needed, until its stop or target (the EA's cutoff hour).
    position_exit: str = "session_exit"
    # "one": the first qualifying breakout ends the session. "one_per_direction": a long and a short
    # may each be taken once per session. "after_close": any fresh breakout (previous entry bar
    # closed inside) is taken whenever the session has no open position, until the cutoff.
    trades_per_session: str = "one"
    # "close": a bar must close beyond the range; entry at the next bar's open. "stop_order": entry
    # at the range edge (plus any buffer) the first minute the ask/bid touches it.
    entry_mode: str = "close"
    # Range size against an intraday ATR (Wilder, on range-timeframe bars, read at the range
    # candle); 1344 = 14 days of 15-minute bars.  null disables each bound.
    or_atr_period: int = 1344
    or_min_atr: float | None = None
    or_max_atr: float | None = None
    or_candle: str = "any"  # any | bullish | bearish (range close vs open)
    # Exits beyond session_exit/stop_or_target: "end_of_day" flattens at flat_time on the session
    # date; "friday_close" holds overnight but flattens at flat_time on the session week's Friday.
    flat_time: str | None = None
    # ATR trailing stop: no fixed target; once price reaches trail_activation_r × the initial
    # risk, the stop trails each entry-bar close by trail_atr_multiple × ATR(trail_atr_period)
    # on entry-timeframe bars and only ever tightens.  null disables it.
    trail_atr_multiple: float | None = None
    trail_activation_r: float = 1.5
    trail_atr_period: int = 14
    # Overnight financing: annual % of entry notional per night held (17:00 New York rollovers,
    # counted in calendar nights so a weekend is three).
    financing_pct_annual: float = 0.0
    # Tweak-study filters and exits (null disables each one):
    # trade only in the direction of the prior day's close vs its N-day simple average;
    trend_sma_days: int | None = None
    # skip a session whose open gaps more than this x prior daily ATR(14) from the price at
    # 16:00 New York on the previous weekday (the cash close);
    gap_max_atr: float | None = None
    # move the stop to the entry price once price has reached this many R in profit.
    breakeven_r: float | None = None
    # Account protection, mirrored by the EA's inputs (null disables each one).
    # Risk drops to risk_throttle_percent while equity is more than risk_throttle_below_pct
    # below the starting balance (initial_equity):
    risk_throttle_below_pct: float | None = None
    risk_throttle_percent: float | None = None
    # no new entries once equity is this % below the balance at the start of the broker day
    # (17:00 New York, midnight on FTMO's server):
    daily_loss_stop_pct: float | None = None
    # no new entries at all once equity is this % below the starting balance.  The EA also
    # closes its open positions there; the backtest lets them run to their own exits.
    equity_stop_pct: float | None = None
    # How cached MT5 bar timestamps are decoded. "stored_as_utc" is the historical behaviour
    # (MT5 epoch seconds taken as UTC); "new_york_plus_7" decodes a broker clock that runs at
    # New York time + 7 h year-round, which is what the FTMO-Demo XAUUSD cache actually holds.
    broker_clock: str = "stored_as_utc"
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
        if self.holdout_opened is not None:
            date.fromisoformat(self.holdout_opened)
            if not start < end:
                raise ValueError("start < development_end is required")
        elif not start < end <= holdout:
            raise ValueError("start < development_end <= holdout_start is required "
                             "(set holdout_opened to read past the holdout boundary)")
        if not start <= tick_start < holdout:
            raise ValueError("tick_start must lie inside development history")
        ZoneInfo(self.session_timezone)
        for label in (self.session_open, self.session_exit):
            time.fromisoformat(label)
        if self.session_exit <= self.session_open:
            raise ValueError("session_exit must follow session_open on the same local date")
        if self.signal_minutes not in SIGNAL_MINUTES:
            raise ValueError("signal_minutes (entry timeframe) must be 1, 5 or 15")
        if self.range_minutes not in RANGE_MINUTES:
            raise ValueError("range_minutes must be 5, 15, 30, 45 or 60")
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
        if self.entry_cutoff_minutes_after_or is not None:
            cutoff = float(self.entry_cutoff_minutes_after_or)
            open_minutes = _minutes(self.session_open)
            exit_minutes = _minutes(self.session_exit)
            latest = exit_minutes - open_minutes - self.range_minutes
            if not 0 < cutoff <= latest:
                raise ValueError("entry_cutoff_minutes_after_or must lie in (0, session length after the range]")
        if self.min_or_width_atr is not None and float(self.min_or_width_atr) < 0:
            raise ValueError("min_or_width_atr must be nonnegative")
        if self.broker_clock not in BROKER_CLOCKS:
            raise ValueError(f"broker_clock must be one of {sorted(BROKER_CLOCKS)}")
        if self.trade_direction not in TRADE_DIRECTIONS:
            raise ValueError(f"trade_direction must be one of {sorted(TRADE_DIRECTIONS)}")
        if self.position_exit not in POSITION_EXITS:
            raise ValueError(f"position_exit must be one of {sorted(POSITION_EXITS)}")
        if self.trades_per_session not in TRADES_PER_SESSION:
            raise ValueError(f"trades_per_session must be one of {sorted(TRADES_PER_SESSION)}")
        if self.entry_mode not in ENTRY_MODES:
            raise ValueError(f"entry_mode must be one of {sorted(ENTRY_MODES)}")
        if self.trades_per_session == "after_close" and self.entry_mode != "close":
            raise ValueError("after_close re-entry needs entry_mode close")
        if type(self.or_atr_period) is not int or self.or_atr_period < 1:
            raise ValueError("or_atr_period must be a positive integer")
        for name in ("or_min_atr", "or_max_atr"):
            if getattr(self, name) is not None and float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if (self.or_min_atr is not None and self.or_max_atr is not None
                and self.or_min_atr >= self.or_max_atr):
            raise ValueError("or_min_atr must be below or_max_atr")
        if self.or_candle not in OR_CANDLES:
            raise ValueError(f"or_candle must be one of {sorted(OR_CANDLES)}")
        if self.position_exit in ("end_of_day", "friday_close"):
            if self.flat_time is None:
                raise ValueError(f"position_exit {self.position_exit} needs flat_time")
            time.fromisoformat(self.flat_time)
            if self.flat_time < self.session_exit:
                raise ValueError("flat_time must not precede session_exit (the entry cutoff)")
        elif self.flat_time is not None:
            raise ValueError("flat_time is only used by end_of_day and friday_close")
        if self.trail_atr_multiple is not None and float(self.trail_atr_multiple) <= 0:
            raise ValueError("trail_atr_multiple must be positive")
        if self.trail_activation_r < 0:
            raise ValueError("trail_activation_r must be nonnegative")
        if type(self.trail_atr_period) is not int or self.trail_atr_period < 1:
            raise ValueError("trail_atr_period must be a positive integer")
        if self.trend_sma_days is not None and (type(self.trend_sma_days) is not int or self.trend_sma_days < 2):
            raise ValueError("trend_sma_days must be an integer of at least 2")
        if self.gap_max_atr is not None and float(self.gap_max_atr) <= 0:
            raise ValueError("gap_max_atr must be positive")
        if self.breakeven_r is not None:
            if float(self.breakeven_r) <= 0:
                raise ValueError("breakeven_r must be positive")
            if self.trail_atr_multiple is not None:
                raise ValueError("breakeven_r and a trailing stop cannot be combined")
        if (self.risk_throttle_below_pct is None) != (self.risk_throttle_percent is None):
            raise ValueError("risk_throttle_below_pct and risk_throttle_percent go together")
        if self.risk_throttle_percent is not None and not 0 < float(self.risk_throttle_percent) <= self.risk_percent:
            raise ValueError("risk_throttle_percent must be positive and at most risk_percent")
        for name in ("risk_throttle_below_pct", "daily_loss_stop_pct", "equity_stop_pct"):
            value = getattr(self, name)
            if value is not None and not 0 < float(value) < 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if self.financing_pct_annual < 0:
            raise ValueError("financing_pct_annual must be nonnegative")


BROKER_CLOCKS = {"stored_as_utc", "new_york_plus_7", "eastern_european"}
RANGE_MINUTES = (5, 15, 30, 45, 60)
SIGNAL_MINUTES = (1, 5, 15)
TRADE_DIRECTIONS = {"both", "long", "short"}
POSITION_EXITS = {"session_exit", "stop_or_target", "end_of_day", "friday_close"}
TRADES_PER_SESSION = {"one", "one_per_direction", "after_close"}
ENTRY_MODES = {"close", "stop_order"}
OR_CANDLES = {"any", "bullish", "bearish"}


def holdout_note(config) -> str:
    """One line for run output: whether, and how far, a run read past the holdout boundary."""
    if date.fromisoformat(config.development_end) <= date.fromisoformat(config.holdout_start):
        return f"{config.holdout_start[:4]}+ final holdout touched: NO"
    return (f"Holdout opened {config.holdout_opened}: this run includes the former holdout "
            f"{config.holdout_start} to {config.development_end} (exclusive); not out-of-sample")


def _minutes(label: str) -> int:
    parsed = time.fromisoformat(label)
    return parsed.hour * 60 + parsed.minute


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
