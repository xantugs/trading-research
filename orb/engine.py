"""Opening Range Breakout backtest engine - the Python twin of ORB_Backtest.mq5.

The rules are the EA's rules, expressed on bar data:

* One opening-range candle per server-time day (hour:minute on the signal
  timeframe).  Its high/low are the range.
* Close-confirmation entry: the previous signal bar had not already closed
  beyond the relevant boundary and the last one closed outside -> market entry
  at the NEXT bar's open (ask for longs = bid + spread, bid for shorts).
* Immediate entry: the first path bar whose ask trades above the range high
  (or bid below the low) -> entry at max(open ask, high + point).
* First confirmed breakout of the day only, regardless of direction. A signal
  vetoed by the trend filter still consumes the day.
* The range can be the observed signal candle or a forecast box centred on the
  session open. Forecasts use the same opening-window range from prior sessions,
  optionally adjusted by today's pre-range realised volatility.
* Close confirmation can require several consecutive closes beyond the range,
  optionally with candle colour matching the breakout direction.
* Stop = range midpoint or far side +/- range x sl_ratio; target = risk x tp_ratio.
* Exits are resolved on the *path* data (ideally M1; the signal bars if that is
  all you have).  MT5 closes longs on the bid and shorts on the ask, so short
  stops/targets are compared against bid + spread.  A bar that touches both the
  stop and the target is settled by ``same_bar_rule`` ("sl_first" is the
  conservative default).  Gaps through a level fill at the bar's open.
* Breakeven / trailing are evaluated once per path bar from that bar's extreme
  and applied from the next bar - a bar-data approximation of the EA's per-tick
  logic.  Reconcile with the EA with these switched off first.

Money is simulated separately from prices: risk % of equity at entry, lot size
rounded down to the step, so the R-multiples never depend on account size.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .data import daily_bars, infer_minutes, moving_average, resample, mt5_atr, validate_bars
from .execution import ExecutionDataError, ExecutionIntent, NbboExecution


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
@dataclass
class Params:
    # timing (server time)
    orb_hour: int = 16
    orb_minute: int = 30
    cutoff_hour: int = 19
    # strategy
    entry_mode: str = "close"          # "close" | "immediate"
    direction: str = "both"            # "both" | "long" | "short"
    range_mode: str = "signal_bar"      # signal_bar | historical_percentile | historical_percentile_premarket
    range_history_sessions: int = 20
    range_percentile: float = 50.0
    range_multiplier: float = 1.0
    premarket_window_minutes: int = 120
    premarket_sensitivity: float = 0.5
    confirmation_bars: int = 1
    confirmation_same_color: bool = False
    tp_ratio: float = 1.5
    sl_ratio: float = 1.0
    stop_mode: str = "range_multiple"   # "range_multiple" | "midpoint"
    risk_pct: float = 2.0
    # end of day
    eod_close: Optional[str] = None    # "22:45" or None
    # opening-range filters
    atr_period: int = 14
    min_range_atr: float = 0.0
    max_range_atr: float = 0.0
    range_lookback_minutes: int = 120
    max_range_lookback_ratio: float = 0.0
    vol_lookback: int = 8
    vol_spike_min: float = 0.0         # > 0: skip days whose range candle has no volume spike
    # higher-timeframe trend filter
    filter_ma_period: int = 0
    filter_tf_minutes: int = 60
    filter_ma_method: str = "ema"      # "ema" | "sma"
    filter_ma_slope_bars: int = 0
    filter_ma_min_slope_pct: float = 0.0
    # exit management (R = initial risk distance)
    breakeven_at_r: float = 0.0
    breakeven_lock_r: float = 0.0
    trail_start_r: float = 0.0
    trail_dist_r: float = 1.0
    trail_step_r: float = 0.1
    # execution model
    spread: float = 0.0                # constant spread in price units (ask = bid + spread)
    use_data_spread: bool = False      # use each bar's recorded spread (points x point) instead
    max_spread: float = 0.0            # skip entry above this price-unit spread (0 = off)
    min_stop_points: int = 0           # minimum initial risk in symbol points (0 = off)
    point: float = 0.01                # symbol point size (NAS100 with 2 digits -> 0.01)
    same_bar_rule: str = "sl_first"    # "sl_first" | "tp_first"
    # money
    initial_balance: float = 10_000.0
    point_value: float = 1.0           # account currency per 1.0 price move per 1.0 lot
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    commission_per_lot: float = 0.0    # per side
    max_gross_leverage: float = 0.0    # cap gross notional / equity; 0 = off

    def eod_minutes(self) -> Optional[int]:
        if self.eod_close is None:
            return None
        if not isinstance(self.eod_close, str):
            raise ValueError("eod_close must be HH:MM or null")
        h, m = self.eod_close.split(":")
        if not 0 <= int(h) <= 23 or not 0 <= int(m) <= 59:
            raise ValueError("eod_close must be a valid HH:MM")
        return int(h) * 60 + int(m)

    def validate(self, tf_minutes: int) -> None:
        defaults = Params().to_dict()
        for key, value in self.to_dict().items():
            default = defaults[key]
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be a boolean")
            elif isinstance(default, (int, float)):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
                    raise ValueError(f"{key} must be a finite number")
                if isinstance(default, int) and not isinstance(value, int):
                    raise ValueError(f"{key} must be an integer")
        if not isinstance(tf_minutes, int) or tf_minutes <= 0 or 1440 % tf_minutes:
            raise ValueError("Invalid signal timeframe")
        if not 0 <= self.orb_hour <= 23 or not 1 <= self.cutoff_hour <= 23:
            raise ValueError("Range/cutoff hour is out of bounds")
        if not 0 <= self.orb_minute <= 59:
            raise ValueError("orb_minute must be 0-59")
        if (self.orb_hour * 60 + self.orb_minute) % tf_minutes != 0:
            raise ValueError(f"orb_minute must sit on a {tf_minutes}-minute bar boundary")
        if self.orb_hour >= self.cutoff_hour:
            raise ValueError("orb_hour must be earlier than cutoff_hour")
        if self.tp_ratio <= 0 or self.sl_ratio <= 0:
            raise ValueError("tp_ratio and sl_ratio must be positive")
        if not 0 < self.risk_pct <= 100:
            raise ValueError("risk_pct must be in (0, 100]")
        eod = self.eod_minutes()
        if eod is not None and eod < self.cutoff_hour * 60:
            raise ValueError("eod_close must not be earlier than the cutoff hour")
        if self.entry_mode not in ("close", "immediate"):
            raise ValueError("entry_mode must be 'close' or 'immediate'")
        range_modes = ("signal_bar", "historical_percentile", "historical_percentile_premarket")
        if self.range_mode not in range_modes:
            raise ValueError(f"range_mode must be one of {range_modes}")
        if self.stop_mode not in ("range_multiple", "midpoint"):
            raise ValueError("stop_mode must be 'range_multiple' or 'midpoint'")
        if self.direction not in ("both", "long", "short"):
            raise ValueError("direction must be 'both', 'long' or 'short'")
        if self.trail_start_r > 0 and self.trail_dist_r <= 0:
            raise ValueError("trail_dist_r must be positive when trailing is on")
        if self.same_bar_rule not in ("sl_first", "tp_first"):
            raise ValueError("Invalid same_bar_rule")
        if self.filter_ma_method not in ("ema", "sma"):
            raise ValueError("Invalid filter_ma_method")
        if self.confirmation_bars < 1:
            raise ValueError("confirmation_bars must be positive")
        if self.range_history_sessions < 1:
            raise ValueError("range_history_sessions must be positive")
        if not 0 < self.range_percentile <= 100:
            raise ValueError("range_percentile must be in (0, 100]")
        if self.range_multiplier <= 0:
            raise ValueError("range_multiplier must be positive")
        if self.premarket_window_minutes < 1:
            raise ValueError("premarket_window_minutes must be positive")
        if self.premarket_sensitivity < 0:
            raise ValueError("premarket_sensitivity must be nonnegative")
        if self.range_lookback_minutes < 1:
            raise ValueError("range_lookback_minutes must be positive")
        if self.filter_ma_min_slope_pct > 0 and self.filter_ma_slope_bars < 1:
            raise ValueError("filter_ma_slope_bars must be positive when the slope filter is on")
        if self.filter_ma_min_slope_pct > 0 and self.filter_ma_period < 1:
            raise ValueError("filter_ma_period must be positive when the slope filter is on")
        if self.filter_tf_minutes < tf_minutes or self.filter_tf_minutes % tf_minutes or 1440 % self.filter_tf_minutes:
            raise ValueError("MA timeframe must be a multiple of signal timeframe and divide 1440")
        for name in ("initial_balance", "point", "point_value", "lot_step", "min_lot", "max_lot"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("spread", "max_spread", "min_stop_points", "commission_per_lot",
                     "max_gross_leverage", "atr_period", "min_range_atr", "max_range_atr",
                     "vol_lookback", "vol_spike_min", "filter_ma_period", "filter_ma_slope_bars",
                     "filter_ma_min_slope_pct", "max_range_lookback_ratio",
                     "breakeven_at_r",
                     "breakeven_lock_r", "trail_start_r", "trail_dist_r", "trail_step_r"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.min_lot > self.max_lot:
            raise ValueError("min_lot exceeds max_lot")
        if self.max_range_atr and self.min_range_atr > self.max_range_atr:
            raise ValueError("min_range_atr exceeds max_range_atr")

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    params: Params
    trades: pd.DataFrame          # one row per trade
    days: pd.DataFrame            # one row per day with an opening-range candle
    equity: pd.Series             # balance after each closed trade (first point = initial balance)
    stats: dict = field(default_factory=dict)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)

    def by(self, key: str) -> pd.DataFrame:
        """Grouped summary: key in {'year', 'entry_hour', 'weekday', 'reason', 'direction'}."""
        t = self.trades
        if t.empty:
            return pd.DataFrame()
        if key == "year":
            g = t["entry_time"].dt.year
        elif key == "entry_hour":
            g = t["entry_time"].dt.hour
        elif key == "weekday":
            g = t["entry_time"].dt.day_name()
        else:
            g = t[key]
        out = t.groupby(g).agg(
            trades=("r", "size"),
            win_rate=("r", lambda s: float((s > 0).mean())),
            avg_r=("r", "mean"),
            total_r=("r", "sum"),
            profit=("profit", "sum"),
        )
        if key == "weekday":
            order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            out = out.reindex([d for d in order if d in out.index])
        return out


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def run_backtest(bars: pd.DataFrame, params: Params, path: Optional[pd.DataFrame] = None,
                 execution: Optional[NbboExecution] = None,
                 progress: Optional[Callable[[int, int, str], None]] = None) -> Result:
    """Run the strategy.

    bars : signal-timeframe bars (e.g. M15).  If you only have M1 data, pass the
           M1 frame as ``path`` and ``resample(m1, 15)`` as ``bars``.
    path : finer bars used to resolve exits / immediate entries (M1 ideally).
           Defaults to ``bars``.
    execution : optional strict NBBO top-of-book backend.  Signal and indicator
                rules still use ``bars``/``path``; entry and exit prices use the
                ordered quote stream.  Milestone one supports close-confirmed,
                fixed-stop/target, end-of-day-flat runs only.
    """
    p = params
    if path is None:
        path = bars
    validate_bars(bars)
    validate_bars(path)
    if not bars.index.is_monotonic_increasing or not path.index.is_monotonic_increasing:
        raise ValueError("Bars and path must be sorted")
    if path.index[-1] < bars.index[-1]:
        raise ValueError("Exit path ends before the last signal bar")
    tf = infer_minutes(bars)
    p.validate(tf)
    if execution is not None:
        if p.entry_mode != "close":
            raise ValueError("Full tick mode currently supports close-confirmed entries only")
        if p.eod_close is None:
            raise ValueError("Full tick mode currently requires an end-of-day close")
        if p.breakeven_at_r > 0 or p.trail_start_r > 0:
            raise ValueError("Full tick mode currently requires breakeven and trailing to be off")

    sig = _Arrays(bars)
    pth = _Arrays(path)
    n_sig = len(sig.t)

    eod = p.eod_minutes()
    allow_long = p.direction in ("both", "long")
    allow_short = p.direction in ("both", "short")

    # daily ATR of the previous completed day, keyed by day
    atr_by_day: dict = {}
    if p.atr_period > 0:
        d = daily_bars(path)
        atr = mt5_atr(d, p.atr_period).shift(1)
        atr_by_day = {k.to_datetime64(): v for k, v in atr.items() if pd.notna(v)}

    trend = _TrendFilter(bars, p) if p.filter_ma_period > 0 else None

    path_minutes = infer_minutes(path)

    # opening-range candle of each day
    orb_mask = (sig.hour == p.orb_hour) & (sig.minute == p.orb_minute)
    orb_indices = np.flatnonzero(orb_mask)
    observed_widths = np.array([
        (sig.h[i] - sig.l[i]) / sig.o[i] if sig.o[i] > 0 else np.nan
        for i in orb_indices
    ])
    premarket_sigmas = (np.array([
        _realized_sigma(path, pd.Timestamp(sig.t[i]), p.premarket_window_minutes, path_minutes)
        for i in orb_indices
    ]) if p.range_mode == "historical_percentile_premarket" else np.full(len(orb_indices), np.nan))

    trades: list[dict] = []
    days: list[dict] = []
    events: list[dict] = []

    def add_event(when, kind: str, detail: str, direction="", price=np.nan, status="info") -> None:
        stamp = pd.Timestamp(when)
        events.append({"date": stamp.date(), "time": stamp, "event": kind, "direction": direction,
                       "price": price, "status": status, "detail": detail})

    for session_i, orb_i in enumerate(orb_indices):
        day = sig.day[orb_i]
        orb_time = pd.Timestamp(sig.t[orb_i])
        if progress is not None:
            progress(session_i, len(orb_indices), orb_time.date().isoformat())
        prior_path = path.loc[path.index < orb_time]
        range_note = "Observed opening range"
        if p.range_mode != "signal_bar":
            start = session_i - p.range_history_sessions
            previous = observed_widths[max(0, start):session_i]
            if len(previous) < p.range_history_sessions or not np.isfinite(previous).all():
                add_event(orb_time, "range_rejected", "Insufficient prior-session range history", status="rejected")
                days.append({
                    "date": orb_time.date(), "orb_time": orb_time,
                    "orb_high": np.nan, "orb_low": np.nan, "orb_range": np.nan,
                    "range_mode": "Historical opening-range percentile",
                    "range_lookback_ratio": np.nan, "range_atr": 0.0, "vol_ratio": 0.0,
                    "status": "skipped: insufficient session history", "long": "", "short": "",
                })
                continue
            width_pct = float(np.percentile(previous, p.range_percentile))
            adjustment = 1.0
            if p.range_mode == "historical_percentile_premarket":
                prior_sigma = premarket_sigmas[start:session_i]
                today_sigma = premarket_sigmas[session_i]
                reference_sigma = float(np.median(prior_sigma)) if np.isfinite(prior_sigma).all() else np.nan
                if not np.isfinite(today_sigma) or not np.isfinite(reference_sigma) or reference_sigma <= 0:
                    add_event(orb_time, "range_rejected", "Incomplete premarket volatility history", status="rejected")
                    days.append({
                        "date": orb_time.date(), "orb_time": orb_time,
                        "orb_high": np.nan, "orb_low": np.nan, "orb_range": np.nan,
                        "range_mode": "Historical percentile + premarket volatility",
                        "range_lookback_ratio": np.nan, "range_atr": 0.0, "vol_ratio": 0.0,
                        "status": "skipped: insufficient premarket history", "long": "", "short": "",
                    })
                    continue
                adjustment = (today_sigma / reference_sigma) ** p.premarket_sensitivity
            anchor = float(sig.o[orb_i])
            width = anchor * width_pct * p.range_multiplier * adjustment
            o_h, o_l = anchor + width / 2, anchor - width / 2
            range_note = f"Historical P{p.range_percentile:g} x {p.range_multiplier:g}"
            if p.range_mode == "historical_percentile_premarket":
                range_note += f" x premarket {adjustment:.2f}"
        else:
            o_h, o_l = sig.h[orb_i], sig.l[orb_i]
        rng = o_h - o_l
        history_start = orb_time - pd.Timedelta(minutes=p.range_lookback_minutes)
        history = prior_path.loc[prior_path.index >= history_start]
        history_complete = bool(
            len(history)
            and history.index[0] <= history_start + pd.Timedelta(minutes=path_minutes)
            and history.index[-1] >= orb_time - pd.Timedelta(minutes=path_minutes)
        )
        history_span = float(history.high.max() - history.low.min()) if len(history) else 0.0
        lookback_ratio = rng / history_span if history_complete and history_span > 0 else np.nan
        rec = {
            "date": pd.Timestamp(day).date(),
            "orb_time": pd.Timestamp(sig.t[orb_i]),
            "orb_high": o_h,
            "orb_low": o_l,
            "orb_range": rng,
            "range_mode": range_note,
            "range_lookback_ratio": lookback_ratio,
            "range_atr": 0.0,
            "vol_ratio": 0.0,
            "status": "no signal",
            "long": "",
            "short": "",
        }
        days.append(rec)
        range_ready = orb_time + (pd.Timedelta(minutes=tf) if p.range_mode == "signal_bar" else pd.Timedelta(0))
        add_event(range_ready, "range_ready",
                  f"{range_note}: {o_l:.5f} to {o_h:.5f} ({rng:.5f} wide)")

        if rng <= 0:
            rec["status"] = "skipped: zero range"
            add_event(range_ready, "range_rejected", "Range width is zero", status="rejected")
            continue
        if p.max_range_lookback_ratio > 0:
            if not history_complete or not np.isfinite(lookback_ratio):
                rec["status"] = "skipped: insufficient range lookback history"
                add_event(range_ready, "range_rejected", "Incomplete prior-window history", status="rejected")
                continue
            if lookback_ratio > p.max_range_lookback_ratio:
                rec["status"] = "skipped: range too large vs lookback"
                add_event(range_ready, "range_rejected",
                          f"Range/prior-window ratio {lookback_ratio:.3f} exceeds {p.max_range_lookback_ratio:g}",
                          status="rejected")
                continue

        atr_val = atr_by_day.get(day)
        range_atr = rng / atr_val if atr_val and atr_val > 0 else 0.0
        rec["range_atr"] = range_atr

        vol_ratio = 0.0
        # A forecast box is tradeable at the opening timestamp, before that
        # candle's final volume exists. Keep the observed-candle volume check
        # out of forecast modes so it cannot look ahead.
        if p.range_mode == "signal_bar" and p.vol_lookback > 0 and orb_i >= p.vol_lookback:
            prev_mean = sig.v[orb_i - p.vol_lookback : orb_i].mean()
            if prev_mean > 0:
                vol_ratio = sig.v[orb_i] / prev_mean
        rec["vol_ratio"] = vol_ratio

        if p.vol_spike_min > 0 and vol_ratio > 0 and vol_ratio < p.vol_spike_min:
            rec["status"] = "skipped: no volume spike"
            add_event(range_ready, "range_rejected",
                      f"Volume ratio {vol_ratio:.2f} is below {p.vol_spike_min:g}", status="rejected")
            continue
        if range_atr > 0:
            if p.min_range_atr > 0 and range_atr < p.min_range_atr:
                rec["status"] = "skipped: range too small"
                add_event(range_ready, "range_rejected", "Range is below the minimum daily-ATR ratio",
                          status="rejected")
                continue
            if p.max_range_atr > 0 and range_atr > p.max_range_atr:
                rec["status"] = "skipped: range too large"
                add_event(range_ready, "range_rejected", "Range exceeds the maximum daily-ATR ratio",
                          status="rejected")
                continue

        trade_taken = False

        def open_trade(direction: int, entry: float, entry_time: np.datetime64, path_start: int,
                       signal_time: np.datetime64) -> None:
            if path_start >= len(pth.t) or pth.t[path_start] != entry_time:
                raise ValueError(f"Exit path is missing the entry bar at {entry_time}")
            if direction > 0:
                sl = (o_h + o_l) / 2 if p.stop_mode == "midpoint" else o_h - rng * p.sl_ratio
                tp = entry + (entry - sl) * p.tp_ratio
            else:
                sl = (o_h + o_l) / 2 if p.stop_mode == "midpoint" else o_l + rng * p.sl_ratio
                tp = entry - (sl - entry) * p.tp_ratio
            side = "long" if direction > 0 else "short"
            reference_entry = entry
            reference_risk = abs(reference_entry - sl)
            reference_exit_time = reference_exit = reference_r = np.nan
            reference_reason = "entry beyond stop"
            reference_ambiguous = False
            reference_ambiguous_time = None
            if (direction > 0 and sl < reference_entry) or (direction < 0 and sl > reference_entry):
                reference_tp = reference_entry + direction * reference_risk * p.tp_ratio
                (reference_exit_time, reference_exit, reference_reason, reference_ambiguous,
                 reference_ambiguous_time) = _resolve_exit(
                    pth, path_start, direction, reference_entry, sl, reference_tp, p, eod,
                    intrabar_entry=False,
                )
                reference_r = (reference_exit - reference_entry) * direction / reference_risk

            execution_fields = {
                "execution_model": "m1_bars",
                "order_activation_time": pd.Timestamp(entry_time),
                "reference_entry": reference_entry,
                "reference_exit_time": pd.Timestamp(reference_exit_time)
                if not pd.isna(reference_exit_time) else pd.NaT,
                "reference_exit": reference_exit,
                "reference_reason": reference_reason,
                "reference_r": reference_r,
                "m1_same_bar_ambiguous": reference_ambiguous,
                "m1_ambiguous_bar_time": pd.Timestamp(reference_ambiguous_time)
                if reference_ambiguous_time is not None else pd.NaT,
                "entry_event_id": np.nan,
                "entry_bid": np.nan,
                "entry_ask": np.nan,
                "entry_spread": _spread_at(pth, p, index=path_start),
                "entry_delay_ms": 0.0,
                "entry_slippage_points": 0.0,
                "initial_sl": sl,
                "final_sl": sl,
                "exit_event_id": np.nan,
                "exit_bid": np.nan,
                "exit_ask": np.nan,
                "exit_trigger_level": np.nan,
                "exit_slippage_points": np.nan,
                "eod_delay_ms": 0.0 if reference_reason == "EOD" else np.nan,
                "mfe_time": pd.NaT,
                "mfe_event_id": np.nan,
                "mfe_price": np.nan,
                "mae_time": pd.NaT,
                "mae_event_id": np.nan,
                "mae_price": np.nan,
                "quote_events_processed": 0,
                "max_interquote_gap_ms": np.nan,
                "tick_source_file": "",
                "execution_status": "executed",
            }

            if execution is None:
                if (direction > 0 and sl >= entry) or (direction < 0 and sl <= entry):
                    rec[side] = "skipped: entry beyond stop"
                    add_event(entry_time, "entry_rejected", "Entry is beyond the initial stop",
                              side, entry, "rejected")
                    return
                entry_spread = _spread_at(pth, p, index=path_start)
                if p.max_spread > 0 and entry_spread > p.max_spread:
                    rec[side] = "skipped: spread too wide"
                    add_event(entry_time, "entry_rejected",
                              f"Spread {entry_spread:.5f} exceeds {p.max_spread:g}",
                              side, entry, "rejected")
                    return
                risk = abs(entry - sl)
                if p.min_stop_points > 0 and risk < p.min_stop_points * p.point:
                    rec[side] = "skipped: stop too close"
                    add_event(entry_time, "entry_rejected",
                              f"Initial risk is below {p.min_stop_points} points",
                              side, entry, "rejected")
                    return
                opening_quote = pth.o[path_start] + (entry_spread if direction > 0 else 0)
                intrabar_entry = p.entry_mode == "immediate" and abs(entry - opening_quote) > 1e-9
                exit_time, exit_price, reason, ambiguous, ambiguous_time = _resolve_exit(
                    pth, path_start, direction, entry, sl, tp, p, eod, intrabar_entry=intrabar_entry)
                exit_index = min(int(np.searchsorted(pth.t, exit_time, side="right")) - 1, len(pth.t) - 1)
                mfe_r, mae_r = _excursions(pth, path_start, exit_index, direction, entry, risk, p)
            else:
                scheduled = pd.Timestamp(entry_time)
                eod_time = scheduled.normalize() + pd.Timedelta(minutes=eod)
                intent = ExecutionIntent(
                    direction=direction,
                    signal_time=pd.Timestamp(signal_time),
                    activation_time=scheduled,
                    stop_loss=sl,
                    target_ratio=p.tp_ratio,
                    eod_time=eod_time,
                    point=p.point,
                    reference_entry=reference_entry,
                    max_spread=p.max_spread,
                    min_stop_distance=p.min_stop_points * p.point,
                    entry_mode=p.entry_mode,
                    breakeven_at_r=p.breakeven_at_r,
                    trail_start_r=p.trail_start_r,
                )
                try:
                    outcome = execution.execute(intent)
                except ExecutionDataError as exc:
                    raise ValueError(
                        f"NBBO replay failed for {rec['date']} {side}: {exc.code}: {exc.detail}"
                    ) from exc
                if not outcome.executed:
                    rec[side] = f"skipped: {outcome.detail}"
                    rec["status"] = "signal consumed; execution rejected"
                    add_event(outcome.entry_time or scheduled, "entry_rejected", outcome.detail,
                              side, outcome.entry or np.nan, "rejected")
                    return
                entry = float(outcome.entry)
                risk = float(outcome.risk_dist)
                tp = float(outcome.target)
                exit_time = outcome.exit_time.to_datetime64()
                exit_price = float(outcome.exit)
                reason = str(outcome.exit_reason)
                ambiguous = False
                ambiguous_time = None
                mfe_r, mae_r = float(outcome.mfe_r), float(outcome.mae_r)
                execution_fields.update({
                    "execution_model": outcome.execution_model,
                    "order_activation_time": outcome.order_activation_time,
                    "entry_event_id": outcome.entry_event_id,
                    "entry_bid": outcome.entry_bid,
                    "entry_ask": outcome.entry_ask,
                    "entry_spread": outcome.entry_spread,
                    "entry_delay_ms": outcome.entry_delay_ms,
                    "entry_slippage_points": outcome.entry_slippage_points,
                    "initial_sl": outcome.initial_sl,
                    "final_sl": outcome.final_sl,
                    "exit_event_id": outcome.exit_event_id,
                    "exit_bid": outcome.exit_bid,
                    "exit_ask": outcome.exit_ask,
                    "exit_trigger_level": outcome.exit_trigger_level,
                    "exit_slippage_points": outcome.exit_slippage_points,
                    "eod_delay_ms": outcome.eod_delay_ms,
                    "mfe_time": outcome.mfe_time,
                    "mfe_event_id": outcome.mfe_event_id,
                    "mfe_price": outcome.mfe_price,
                    "mae_time": outcome.mae_time,
                    "mae_event_id": outcome.mae_event_id,
                    "mae_price": outcome.mae_price,
                    "quote_events_processed": outcome.quote_events_processed,
                    "max_interquote_gap_ms": outcome.max_interquote_gap_ms,
                    "tick_source_file": str(outcome.source_path or ""),
                    "execution_status": outcome.status,
                })
            trades.append({
                "date": rec["date"],
                "direction": "long" if direction > 0 else "short",
                "dir": direction,
                "orb_time": rec["orb_time"],
                "orb_high": o_h,
                "orb_low": o_l,
                "orb_range": rng,
                "range_mode": range_note,
                "range_lookback_ratio": lookback_ratio,
                "range_atr": range_atr,
                "vol_ratio": vol_ratio,
                "signal_time": pd.Timestamp(signal_time),
                "entry_time": pd.Timestamp(outcome.entry_time) if execution is not None else pd.Timestamp(entry_time),
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "risk_dist": risk,
                "exit_time": pd.Timestamp(exit_time),
                "exit": exit_price,
                "reason": reason,
                "same_bar_ambiguous": ambiguous,
                "ambiguous_bar_time": pd.Timestamp(ambiguous_time) if ambiguous_time is not None else pd.NaT,
                "r": (exit_price - entry) * direction / risk,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                **execution_fields,
            })
            actual_entry_time = pd.Timestamp(outcome.entry_time) if execution is not None else pd.Timestamp(entry_time)
            add_event(actual_entry_time, "entry", f"Entered {side}; daily trade locked", side, entry, "accepted")
            if execution is not None and reference_ambiguous:
                add_event(reference_ambiguous_time, "m1_ambiguity_resolved",
                          f"M1 touched SL and TP; ordered NBBO exited by {reason}", side,
                          exit_price, "accepted")
            if ambiguous:
                add_event(ambiguous_time, "same_bar_ambiguous",
                          f"SL and TP touched; resolved with {p.same_bar_rule}", side, status="warning")
            add_event(exit_time, "exit", f"Exited by {reason}", side, exit_price, "accepted")
            rec["long" if direction > 0 else "short"] = reason
            rec["status"] = "traded"

        if p.entry_mode == "close":
            i = orb_i if p.range_mode != "signal_bar" else orb_i + 1
            while i + 1 < n_sig and sig.day[i + 1] == day:
                if sig.hour[i + 1] >= p.cutoff_hour:       # the entry bar would be at/after the cutoff
                    break
                direction = 0
                first = i - p.confirmation_bars + 1
                before = first - 1
                if first < 0 or sig.day[first] != day:
                    i += 1
                    continue
                long_closes = bool(np.all(sig.c[first:i + 1] > o_h))
                short_closes = bool(np.all(sig.c[first:i + 1] < o_l))
                if p.confirmation_same_color:
                    long_closes &= bool(np.all(sig.c[first:i + 1] > sig.o[first:i + 1]))
                    short_closes &= bool(np.all(sig.c[first:i + 1] < sig.o[first:i + 1]))
                if (not trade_taken and allow_long and before >= 0 and sig.c[before] <= o_h
                        and long_closes):
                    direction = 1
                elif (not trade_taken and allow_short and before >= 0 and sig.c[before] >= o_l
                      and short_closes):
                    direction = -1
                if direction != 0:
                    trade_taken = True
                    side = "long" if direction > 0 else "short"
                    add_event(sig.t[i], "signal", f"Confirmed {side} breakout", side, sig.c[i], "accepted")
                    entry_time = sig.t[i + 1]
                    signal_close = sig.c[i]
                    if trend is not None and not trend.allows(direction, signal_close, entry_time):
                        rec["long" if direction > 0 else "short"] = "filtered"
                        add_event(entry_time, "entry_rejected", "Trend filter vetoed the first confirmed signal",
                                  side, signal_close, "rejected")
                    else:
                        spread = _spread_at(pth, p, entry_time)
                        entry = sig.o[i + 1] + (spread if direction > 0 else 0.0)
                        path_start = int(np.searchsorted(pth.t, entry_time))
                        open_trade(direction, entry, entry_time, path_start, sig.t[i])
                elif not trade_taken:
                    if sig.h[i] > o_h and sig.c[i] <= o_h:
                        add_event(sig.t[i], "wick_rejected", "High crossed the range but the candle did not close above",
                                  "long", sig.h[i], "rejected")
                    if sig.l[i] < o_l and sig.c[i] >= o_l:
                        add_event(sig.t[i], "wick_rejected", "Low crossed the range but the candle did not close below",
                                  "short", sig.l[i], "rejected")
                elif sig.c[i] > o_h or sig.c[i] < o_l:
                    side = "long" if sig.c[i] > o_h else "short"
                    if not any(e["date"] == rec["date"] and e["event"] == "daily_lock" for e in events):
                        add_event(sig.t[i], "daily_lock", f"Ignored later {side} breakout; daily trade already used",
                                  side, sig.c[i], "rejected")
                i += 1
        else:
            range_ready = sig.t[orb_i] if p.range_mode != "signal_bar" else sig.t[orb_i] + np.timedelta64(tf, "m")
            j = int(np.searchsorted(pth.t, range_ready))
            while j < len(pth.t) and pth.day[j] == day and pth.hour[j] < p.cutoff_hour:
                spread = _spread_at(pth, p, index=j)
                if not trade_taken and allow_long and pth.h[j] + spread > o_h:
                    trade_taken = True
                    add_event(pth.t[j], "signal", "Immediate long crossing", "long", pth.h[j] + spread, "accepted")
                    entry = max(pth.o[j] + spread, o_h + p.point)
                    if trend is not None and not trend.allows(1, entry, pth.t[j]):
                        rec["long"] = "filtered"
                        add_event(pth.t[j], "entry_rejected", "Trend filter vetoed the first crossing",
                                  "long", entry, "rejected")
                    else:
                        open_trade(1, entry, pth.t[j], j, pth.t[j])
                elif not trade_taken and allow_short and pth.l[j] < o_l:
                    trade_taken = True
                    add_event(pth.t[j], "signal", "Immediate short crossing", "short", pth.l[j], "accepted")
                    entry = min(pth.o[j], o_l - p.point)
                    if trend is not None and not trend.allows(-1, entry, pth.t[j]):
                        rec["short"] = "filtered"
                        add_event(pth.t[j], "entry_rejected", "Trend filter vetoed the first crossing",
                                  "short", entry, "rejected")
                    else:
                        open_trade(-1, entry, pth.t[j], j, pth.t[j])
                if trade_taken:
                    break
                j += 1

    trade_columns = ["date", "direction", "dir", "orb_time", "orb_high", "orb_low", "orb_range",
                     "range_mode", "range_lookback_ratio", "range_atr", "vol_ratio", "signal_time",
                     "entry_time", "entry", "sl", "tp", "risk_dist", "exit_time", "exit", "reason",
                     "same_bar_ambiguous", "ambiguous_bar_time", "r", "mfe_r", "mae_r",
                     "execution_model", "order_activation_time", "reference_entry", "reference_exit_time",
                     "reference_exit", "reference_reason", "reference_r", "m1_same_bar_ambiguous",
                     "m1_ambiguous_bar_time", "entry_event_id", "entry_bid", "entry_ask", "entry_spread",
                     "entry_delay_ms", "entry_slippage_points", "initial_sl", "final_sl", "exit_event_id",
                     "exit_bid", "exit_ask", "exit_trigger_level", "exit_slippage_points", "eod_delay_ms",
                     "mfe_time", "mfe_event_id", "mfe_price", "mae_time", "mae_event_id", "mae_price",
                     "quote_events_processed", "max_interquote_gap_ms", "tick_source_file", "execution_status"]
    if progress is not None:
        progress(len(orb_indices), len(orb_indices), "complete")
    trades_df = pd.DataFrame(trades, columns=trade_columns)
    days_df = pd.DataFrame(days)
    trades_df, equity = _simulate_money(trades_df, p, start_time=bars.index[0])
    events_df = pd.DataFrame(events, columns=["date", "time", "event", "direction", "price", "status", "detail"])
    stats = compute_stats(trades_df, equity, p, days_df)
    return Result(params=p, trades=trades_df, days=days_df, equity=equity, stats=stats, events=events_df)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
class _Arrays:
    """numpy views of a bar frame, plus time decompositions."""

    def __init__(self, df: pd.DataFrame):
        idx = pd.DatetimeIndex(df.index)
        self.t = idx.values.astype("datetime64[s]")
        self.day = idx.normalize().values.astype("datetime64[s]")
        self.hour = idx.hour.values
        self.minute = idx.minute.values
        self.tod = self.hour * 60 + self.minute
        self.o = df["open"].values.astype(float)
        self.h = df["high"].values.astype(float)
        self.l = df["low"].values.astype(float)
        self.c = df["close"].values.astype(float)
        self.v = df["tick_volume"].values.astype(float) if "tick_volume" in df else np.zeros(len(df))
        self.spread = df["spread"].values.astype(float) if "spread" in df else np.zeros(len(df))


def _realized_sigma(path: pd.DataFrame, end: pd.Timestamp, minutes: int, bar_minutes: int) -> float:
    """Realised log-return volatility in the complete window ending at ``end``."""
    start = end - pd.Timedelta(minutes=minutes)
    window = path.loc[(path.index >= start) & (path.index < end)]
    if (window.empty
            or window.index[0] > start + pd.Timedelta(minutes=bar_minutes)
            or window.index[-1] < end - pd.Timedelta(minutes=bar_minutes)):
        return np.nan
    prices = np.r_[float(window.open.iloc[0]), window.close.to_numpy(dtype=float)]
    if np.any(prices <= 0):
        return np.nan
    returns = np.diff(np.log(prices))
    return float(np.sqrt(np.sum(returns * returns)))


def _spread_at(pth: _Arrays, p: Params, when: Optional[np.datetime64] = None, index: Optional[int] = None) -> float:
    """Spread in price units for a moment in time (constant unless use_data_spread)."""
    if not p.use_data_spread:
        return p.spread
    if index is None:
        index = min(int(np.searchsorted(pth.t, when)), len(pth.t) - 1)
    return float(pth.spread[index]) * p.point


def _excursions(pth: _Arrays, start: int, end: int, direction: int, entry: float,
                risk: float, p: Params) -> tuple[float, float]:
    """Maximum favourable/adverse excursion through the exit bar, in initial R."""
    indices = range(start, end + 1)
    if direction > 0:
        best = max(float(pth.h[i]) - entry for i in indices)
        worst = max(entry - float(pth.l[i]) for i in indices)
    else:
        lows = [float(pth.l[i]) + _spread_at(pth, p, index=i) for i in indices]
        highs = [float(pth.h[i]) + _spread_at(pth, p, index=i) for i in indices]
        best = entry - min(lows)
        worst = max(highs) - entry
    return max(best, 0.0) / risk, max(worst, 0.0) / risk


def _resolve_exit(pth: _Arrays, start: int, d: int, entry: float, sl: float, tp: float, p: Params, eod: Optional[int],
                  intrabar_entry: bool = False):
    """Walk the path bars from ``start`` until the trade closes."""
    risk = abs(entry - sl)
    sl_cur = sl
    manage = p.breakeven_at_r > 0 or p.trail_start_r > 0
    n = len(pth.t)
    j = start
    while j < n:
        spread = _spread_at(pth, p, index=j) if d < 0 else 0.0
        o, h, l = pth.o[j] + spread, pth.h[j] + spread, pth.l[j] + spread   # bid for longs, ask for shorts

        # end-of-day close happens on the first tick at/after the close time
        if eod is not None and pth.tod[j] >= eod:
            return pth.t[j], o, "EOD", False, None

        if d > 0:
            hit_sl, hit_tp = l <= sl_cur, h >= tp
            sl_px = o if o <= sl_cur else sl_cur
            tp_px = o if o >= tp else tp
            gap_sl, gap_tp = o <= sl_cur, o >= tp
        else:
            hit_sl, hit_tp = h >= sl_cur, l <= tp
            sl_px = o if o >= sl_cur else sl_cur
            tp_px = o if o <= tp else tp
            gap_sl, gap_tp = o >= sl_cur, o <= tp

        if intrabar_entry and j == start:
            # The bar open occurred before the crossing. It is not an executable
            # post-entry gap price. Extremes remain ambiguous under same_bar_rule.
            gap_sl = gap_tp = False
            sl_px, tp_px = sl_cur, tp
        if hit_sl and hit_tp:
            if gap_sl and not gap_tp:
                return pth.t[j], sl_px, "SL", False, None
            if gap_tp and not gap_sl:
                return pth.t[j], tp_px, "TP", False, None
            if p.same_bar_rule == "tp_first":
                return pth.t[j], tp_px, "TP", True, pth.t[j]
            return pth.t[j], sl_px, "SL", True, pth.t[j]
        if hit_sl:
            return pth.t[j], sl_px, "SL", False, None
        if hit_tp:
            return pth.t[j], tp_px, "TP", False, None

        if manage:
            best = (h - entry) if d > 0 else (entry - l)      # best excursion in this bar
            best_r = best / risk
            if p.breakeven_at_r > 0 and best_r >= p.breakeven_at_r:
                cand = entry + d * p.breakeven_lock_r * risk
                if (cand - sl_cur) * d > 0:
                    sl_cur = cand
            if p.trail_start_r > 0 and best_r >= p.trail_start_r:
                cand = (h - p.trail_dist_r * risk) if d > 0 else (l + p.trail_dist_r * risk)
                if (cand - sl_cur) * d >= p.trail_step_r * risk:
                    sl_cur = cand
        j += 1

    # ran out of data: mark to the last close
    last_spread = _spread_at(pth, p, index=n - 1) if d < 0 else 0.0
    return pth.t[n - 1], pth.c[n - 1] + last_spread, "END", False, None


class _TrendFilter:
    """Higher-timeframe moving average, evaluated the way iMA(shift 0) sees it."""

    def __init__(self, bars: pd.DataFrame, p: Params):
        self.p = p
        htf = resample(bars, p.filter_tf_minutes)
        self.closes = htf["close"]
        self.ma_completed = moving_average(self.closes, p.filter_ma_period, p.filter_ma_method)
        self.tf = np.timedelta64(p.filter_tf_minutes, "m")
        self.alpha = 2.0 / (p.filter_ma_period + 1)

    def value(self, price: float, when: np.datetime64) -> Optional[float]:
        ts = pd.Timestamp(when)
        bar_start = ts.floor(f"{self.p.filter_tf_minutes}min")
        completed = self.closes.loc[: bar_start - pd.Timedelta(seconds=1)]
        if len(completed) < self.p.filter_ma_period:
            return None
        if self.p.filter_ma_method == "sma":
            if self.p.filter_ma_period == 1:
                return float(price)
            window = completed.iloc[-(self.p.filter_ma_period - 1):].tolist() + [price]
            return float(np.mean(window))
        prev = float(self.ma_completed.loc[completed.index[-1]])
        return self.alpha * price + (1 - self.alpha) * prev

    def allows(self, direction: int, price: float, when: np.datetime64) -> bool:
        ma = self.value(price, when)
        if ma is None:
            return False                       # the EA also skips when the MA has no value
        price_side = price > ma if direction > 0 else price < ma
        if not price_side or self.p.filter_ma_min_slope_pct <= 0:
            return price_side
        ts = pd.Timestamp(when)
        bar_start = ts.floor(f"{self.p.filter_tf_minutes}min")
        completed = self.closes.loc[: bar_start - pd.Timedelta(seconds=1)]
        n = self.p.filter_ma_slope_bars
        # Lag the slope by one completed HTF bar. This measures the trend that
        # existed before the breakout instead of letting the breakout create
        # the slope used to approve itself.
        if len(completed) < self.p.filter_ma_period + n + 1:
            return False
        latest_ma = float(self.ma_completed.loc[completed.index[-2]])
        previous_ma = float(self.ma_completed.loc[completed.index[-(n + 2)]])
        if not np.isfinite(latest_ma) or not np.isfinite(previous_ma) or previous_ma == 0:
            return False
        slope_pct = (latest_ma - previous_ma) / abs(previous_ma) * 100.0
        return slope_pct >= self.p.filter_ma_min_slope_pct if direction > 0 else (
            slope_pct <= -self.p.filter_ma_min_slope_pct)


def _simulate_money(trades: pd.DataFrame, p: Params, start_time) -> tuple[pd.DataFrame, pd.Series]:
    """Size every trade off the equity at its entry and build the balance curve."""
    eq_times = [pd.Timestamp(start_time)]
    eq_values = [p.initial_balance]
    if trades.empty:
        for col in ("lots", "risk_budget", "actual_risk_money", "actual_risk_pct",
                    "notional", "gross_leverage", "profit", "balance"):
            trades[col] = pd.Series(dtype=float)
        return trades, pd.Series(eq_values, index=pd.DatetimeIndex(eq_times), name="balance")

    trades = trades.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
    # order at the same instant: exits of earlier trades, then entries, then same-bar exits
    events = []
    for i, tr in trades.iterrows():
        events.append((tr["entry_time"], 1, i))
        same_bar = tr["exit_time"] == tr["entry_time"]
        events.append((tr["exit_time"], 2 if same_bar else 0, i))
    events.sort()

    balance = p.initial_balance
    open_idx: set[int] = set()
    lots = np.zeros(len(trades))
    risk_budget = np.zeros(len(trades))
    actual_risk_money = np.zeros(len(trades))
    actual_risk_pct = np.zeros(len(trades))
    notional = np.zeros(len(trades))
    gross_leverage = np.zeros(len(trades))
    profit = np.zeros(len(trades))
    balance_after = np.zeros(len(trades))
    entry = trades["entry"].values
    exit_px = trades["exit"].values
    direction = trades["dir"].values
    risk_dist = trades["risk_dist"].values

    for when, kind, i in events:
        if kind == 1:                                   # entry
            # equity = balance + floating P/L of open trades, marked at this trade's entry price
            floating = sum((entry[i] - entry[k]) * direction[k] * lots[k] * p.point_value for k in open_idx)
            equity = balance + floating
            risk_money = max(equity, 0.0) * p.risk_pct / 100.0
            risk_budget[i] = risk_money
            size = risk_money / (risk_dist[i] * p.point_value) if risk_dist[i] > 0 else 0.0
            if p.max_gross_leverage > 0:
                gross_open = sum(abs(entry[k]) * lots[k] * p.point_value for k in open_idx)
                available = max(equity * p.max_gross_leverage - gross_open, 0.0)
                affordable = available / (abs(entry[i]) * p.point_value) if entry[i] else 0.0
                size = min(size, affordable)
            size = np.floor(size / p.lot_step) * p.lot_step
            size = min(size, p.max_lot)
            # A capital cap must never be defeated by minimum-lot clamping.
            if size < p.min_lot:
                size = 0.0 if p.max_gross_leverage > 0 else p.min_lot
            lots[i] = round(size, 8)
            notional[i] = abs(entry[i]) * lots[i] * p.point_value
            actual_risk_money[i] = risk_dist[i] * lots[i] * p.point_value
            if equity > 0:
                actual_risk_pct[i] = actual_risk_money[i] / equity * 100.0
                gross_leverage[i] = notional[i] / equity
            open_idx.add(i)
        else:
            pnl = (exit_px[i] - entry[i]) * direction[i] * lots[i] * p.point_value
            pnl -= 2 * p.commission_per_lot * lots[i]
            profit[i] = pnl
            balance += pnl
            balance_after[i] = balance
            open_idx.discard(i)
            eq_times.append(when)
            eq_values.append(balance)

    trades["lots"] = lots
    trades["risk_budget"] = risk_budget
    trades["actual_risk_money"] = actual_risk_money
    trades["actual_risk_pct"] = actual_risk_pct
    trades["notional"] = notional
    trades["gross_leverage"] = gross_leverage
    trades["profit"] = profit
    trades["balance"] = balance_after
    equity = pd.Series(eq_values, index=pd.DatetimeIndex(eq_times), name="balance")
    return trades, equity


def compute_stats(trades: pd.DataFrame, equity: pd.Series, p: Params, days: Optional[pd.DataFrame] = None) -> dict:
    s: dict = {"trades": int(len(trades))}
    if days is not None and not days.empty:
        s["days_with_range"] = int(len(days))
        s["days_traded"] = int((days["status"] == "traded").sum())
        s["execution_rejections"] = int((days["status"] == "signal consumed; execution rejected").sum())
    if trades.empty:
        s.update(win_rate=0.0, avg_r=0.0, total_r=0.0, profit_factor=0.0, expectancy_r=0.0,
                 max_dd_pct=0.0, max_dd_r=0.0, final_balance=p.initial_balance, return_pct=0.0,
                 sharpe=0.0, trades_per_year=0.0)
        return s

    r = trades["r"].values
    wins, losses = r[r > 0], r[r <= 0]
    s["win_rate"] = float(len(wins) / len(r))
    s["avg_r"] = float(r.mean())
    s["expectancy_r"] = s["avg_r"]
    s["total_r"] = float(r.sum())
    s["avg_win_r"] = float(wins.mean()) if len(wins) else 0.0
    s["avg_loss_r"] = float(losses.mean()) if len(losses) else 0.0

    pnl = trades["profit"].values
    gross_win, gross_loss = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    s["profit_factor"] = float(gross_win / gross_loss) if gross_loss > 0 else float("inf")

    # drawdowns
    cum_r = np.concatenate([[0.0], np.cumsum(trades.sort_values("exit_time")["r"].values)])
    s["max_dd_r"] = float((np.maximum.accumulate(cum_r) - cum_r).max())
    peak = equity.cummax()
    dd = (equity - peak) / peak
    s["max_dd_pct"] = float(-dd.min() * 100.0)
    s["final_balance"] = float(equity.iloc[-1])
    s["return_pct"] = float((equity.iloc[-1] / p.initial_balance - 1.0) * 100.0)

    # Sharpe from daily P/L relative to the balance at the start of each day
    daily = trades.set_index("exit_time")["profit"].resample("1D").sum()
    if len(daily) > 1:
        start_bal = (p.initial_balance + daily.cumsum().shift(1).fillna(0.0))
        rets = daily / start_bal
        s["sharpe"] = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    else:
        s["sharpe"] = 0.0

    span_days = max((trades["exit_time"].max() - trades["entry_time"].min()).days, 1)
    s["trades_per_year"] = float(len(trades) * 365.0 / span_days)
    s["reasons"] = trades["reason"].value_counts().to_dict()
    if "execution_model" in trades:
        s["execution_models"] = trades["execution_model"].value_counts().to_dict()
        tick = trades[trades["execution_model"] == "nbbo_top_of_book"]
        s["nbbo_trades"] = int(len(tick))
        if not tick.empty:
            s["mean_entry_spread"] = float(tick["entry_spread"].mean())
            s["max_entry_spread"] = float(tick["entry_spread"].max())
            s["mean_entry_delay_ms"] = float(tick["entry_delay_ms"].mean())
            s["max_entry_delay_ms"] = float(tick["entry_delay_ms"].max())
            s["quote_events_processed"] = int(tick["quote_events_processed"].sum())
            s["m1_same_bar_ambiguities"] = int(tick["m1_same_bar_ambiguous"].sum())
            comparable = tick[tick["reference_reason"].isin(["SL", "TP", "EOD"])]
            s["m1_nbbo_exit_disagreements"] = int(
                (comparable["reference_reason"] != comparable["reason"]).sum()
            )
    return s
