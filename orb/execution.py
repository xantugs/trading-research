"""Deterministic top-of-book NBBO execution for confirmed ORB signals.

This module deliberately does not generate signals.  The strategy engine keeps
using minute bars for ranges, confirmation, ATR and trend state, then hands a
close-confirmed :class:`ExecutionIntent` to this backend.  The backend fills and
marks the position from ordered, valid consolidated NBBO snapshots.

The result is exact with respect to the supplied top-of-book quote sequence.  It
is not a promise of a real fill: TAQ does not provide routing latency, queue
position, depth beyond the best quote, or a broker's trigger rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from .ticks import QuoteDay


class ExecutionDataError(RuntimeError):
    """Quote coverage cannot support a strict replay."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class NbboExecutionConfig:
    """Data-timing assumptions for the milestone-one NBBO backend."""

    latency_ms: float = 0.0
    max_entry_delay_ms: float | None = 1_000.0
    max_eod_delay_ms: float | None = 1_000.0
    max_interquote_gap_ms: float | None = None
    strict: bool = True

    def __post_init__(self) -> None:
        for name in ("latency_ms", "max_entry_delay_ms", "max_eod_delay_ms",
                     "max_interquote_gap_ms"):
            value = getattr(self, name)
            if value is not None and (not np.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and nonnegative, or null")
        if not isinstance(self.strict, bool):
            raise TypeError("strict must be boolean")


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    """A confirmed market-entry intent whose initial stop is already known.

    ``signal_time`` is the open time of the confirming bar.  ``activation_time``
    is the boundary at which that completed bar is known and an order can first
    execute.  All timestamps use the same naive strategy clock as the bars.
    """

    direction: int
    signal_time: pd.Timestamp
    activation_time: pd.Timestamp
    stop_loss: float
    target_ratio: float
    eod_time: pd.Timestamp
    point: float = 0.01
    reference_entry: float | None = None
    max_spread: float = 0.0
    min_stop_distance: float = 0.0
    entry_mode: str = "close"
    breakeven_at_r: float = 0.0
    trail_start_r: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.direction, bool) or self.direction not in (-1, 1):
            raise ValueError("direction must be +1 (long) or -1 (short)")
        for name in ("signal_time", "activation_time", "eod_time"):
            value = pd.Timestamp(getattr(self, name))
            if pd.isna(value) or value.tz is not None:
                raise ValueError(f"{name} must be a valid naive strategy-clock timestamp")
            object.__setattr__(self, name, value)
        if self.signal_time > self.activation_time:
            raise ValueError("signal_time cannot follow activation_time")
        if self.eod_time <= self.activation_time:
            raise ValueError("eod_time must follow activation_time")
        for name in ("stop_loss", "target_ratio", "point"):
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("max_spread", "min_stop_distance", "breakeven_at_r", "trail_start_r"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.reference_entry is not None and (
                not np.isfinite(self.reference_entry) or self.reference_entry <= 0):
            raise ValueError("reference_entry must be finite and positive, or null")
        if self.entry_mode != "close":
            raise NotImplementedError("NBBO milestone 1 supports close-confirmed entries only")
        if self.breakeven_at_r > 0 or self.trail_start_r > 0:
            raise NotImplementedError("NBBO milestone 1 does not support breakeven or trailing")


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """One deterministic execution result, including its quote audit trail."""

    status: str
    detail: str
    direction: int
    signal_time: pd.Timestamp
    order_activation_time: pd.Timestamp
    execution_model: str = "nbbo_top_of_book"
    source_path: Path | None = None
    entry_time: pd.Timestamp | None = None
    entry_event_id: int | None = None
    entry_bid: float | None = None
    entry_ask: float | None = None
    entry: float | None = None
    entry_spread: float | None = None
    entry_delay_ms: float | None = None
    reference_entry: float | None = None
    entry_slippage_points: float | None = None
    initial_sl: float | None = None
    final_sl: float | None = None
    target: float | None = None
    risk_dist: float | None = None
    exit_time: pd.Timestamp | None = None
    exit_event_id: int | None = None
    exit_bid: float | None = None
    exit_ask: float | None = None
    exit: float | None = None
    exit_reason: str | None = None
    exit_trigger_level: float | None = None
    exit_slippage_points: float | None = None
    eod_delay_ms: float | None = None
    r_multiple: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    mfe_time: pd.Timestamp | None = None
    mfe_event_id: int | None = None
    mfe_price: float | None = None
    mae_time: pd.Timestamp | None = None
    mae_event_id: int | None = None
    mae_price: float | None = None
    quote_events_processed: int = 0
    max_interquote_gap_ms: float = 0.0

    @property
    def executed(self) -> bool:
        return self.status == "executed"

    def to_dict(self) -> dict:
        """Return a reporting-friendly record without losing nanosecond times."""
        return asdict(self)


class QuoteStore(Protocol):
    """Structural type implemented by :class:`orb.ticks.TickStore`."""

    strategy_offset_hours: int

    def load_day(self, source_date: date) -> QuoteDay: ...


def _milliseconds(later: np.datetime64, earlier: np.datetime64) -> float:
    return float((later - earlier) / np.timedelta64(1, "ms"))


def _failure(intent: ExecutionIntent, activation: pd.Timestamp, config: NbboExecutionConfig,
             code: str, detail: str, source_path: Path | None = None) -> ExecutionOutcome:
    if config.strict:
        raise ExecutionDataError(code, detail)
    return ExecutionOutcome(
        status="unreplayable", detail=f"{code}: {detail}", direction=intent.direction,
        signal_time=intent.signal_time, order_activation_time=activation,
        source_path=source_path, reference_entry=intent.reference_entry,
        initial_sl=intent.stop_loss, final_sl=intent.stop_loss,
    )


def _rejected(intent: ExecutionIntent, activation: pd.Timestamp, code: str, detail: str,
              quote_time: pd.Timestamp, event_id: int, bid: float, ask: float,
              delay_ms: float, source_path: Path | None) -> ExecutionOutcome:
    entry = ask if intent.direction > 0 else bid
    reference_slippage = None if intent.reference_entry is None else (
        (entry - intent.reference_entry) * intent.direction / intent.point
    )
    return ExecutionOutcome(
        status="rejected", detail=f"{code}: {detail}", direction=intent.direction,
        signal_time=intent.signal_time, order_activation_time=activation,
        source_path=source_path, entry_time=quote_time, entry_event_id=event_id,
        entry_bid=bid, entry_ask=ask, entry=entry, entry_spread=ask - bid,
        entry_delay_ms=delay_ms, reference_entry=intent.reference_entry,
        entry_slippage_points=reference_slippage, initial_sl=intent.stop_loss,
        final_sl=intent.stop_loss,
    )


def replay_nbbo(intent: ExecutionIntent, quotes: QuoteDay,
                config: NbboExecutionConfig | None = None) -> ExecutionOutcome:
    """Fill and resolve one close-confirmed intent on ordered NBBO snapshots.

    Entries use the first valid event at or after activation plus configured
    latency.  Longs buy ask and liquidate bid; shorts sell bid and liquidate ask.
    EOD has priority over price triggers at the first quote on/after its time.
    Stop and target jumps fill at the observed executable quote, never at an
    invented trigger-level price.
    """
    config = config or NbboExecutionConfig()
    activation = intent.activation_time + pd.to_timedelta(config.latency_ms, unit="ms")
    source_path = quotes.coverage.source_path
    if len(quotes) == 0:
        return _failure(intent, activation, config, "missing_quotes",
                        "quote day contains no valid NBBO events", source_path)
    if len(quotes.time) != len(quotes.bid) or len(quotes.time) != len(quotes.ask):
        return _failure(intent, activation, config, "invalid_quote_arrays",
                        "quote time/bid/ask arrays have different lengths", source_path)
    if np.any(quotes.time[1:] < quotes.time[:-1]):
        return _failure(intent, activation, config, "unsorted_quotes",
                        "quote events are not in nondecreasing order", source_path)

    activation64 = activation.to_datetime64().astype("datetime64[ns]")
    eod64 = intent.eod_time.to_datetime64().astype("datetime64[ns]")
    entry_id = int(np.searchsorted(quotes.time, activation64, side="left"))
    if entry_id >= len(quotes):
        return _failure(intent, activation, config, "missing_entry_quote",
                        "no valid quote exists at or after order activation", source_path)
    entry_time64 = quotes.time[entry_id]
    entry_time = pd.Timestamp(entry_time64)
    entry_delay = _milliseconds(entry_time64, activation64)
    if config.max_entry_delay_ms is not None and entry_delay > config.max_entry_delay_ms:
        return _failure(
            intent, activation, config, "entry_quote_delayed",
            f"first valid quote is {entry_delay:.3f} ms after activation; "
            f"limit is {config.max_entry_delay_ms:g} ms", source_path,
        )
    if entry_time64 >= eod64:
        bid, ask = float(quotes.bid[entry_id]), float(quotes.ask[entry_id])
        return _rejected(intent, activation, "entry_at_or_after_eod",
                         "the first executable quote is not before EOD",
                         entry_time, entry_id, bid, ask, entry_delay, source_path)

    entry_bid = float(quotes.bid[entry_id])
    entry_ask = float(quotes.ask[entry_id])
    entry = entry_ask if intent.direction > 0 else entry_bid
    spread = entry_ask - entry_bid
    if intent.max_spread > 0 and spread > intent.max_spread:
        return _rejected(intent, activation, "spread_too_wide",
                         f"entry spread {spread:g} exceeds {intent.max_spread:g}",
                         entry_time, entry_id, entry_bid, entry_ask, entry_delay, source_path)

    risk = (entry - intent.stop_loss) * intent.direction
    if risk <= 0:
        return _rejected(intent, activation, "entry_beyond_stop",
                         "actual top-of-book entry is on the wrong side of the supplied stop",
                         entry_time, entry_id, entry_bid, entry_ask, entry_delay, source_path)
    if intent.min_stop_distance > 0 and risk < intent.min_stop_distance:
        return _rejected(intent, activation, "stop_too_close",
                         f"initial risk {risk:g} is below {intent.min_stop_distance:g}",
                         entry_time, entry_id, entry_bid, entry_ask, entry_delay, source_path)
    target = entry + intent.direction * risk * intent.target_ratio

    best_signed = -np.inf
    worst_signed = np.inf
    mfe_id = mae_id = entry_id
    mfe_price = mae_price = entry_bid if intent.direction > 0 else entry_ask
    max_gap_ms = 0.0
    reference_slippage = None if intent.reference_entry is None else (
        (entry - intent.reference_entry) * intent.direction / intent.point
    )

    for event_id in range(entry_id, len(quotes)):
        quote_time64 = quotes.time[event_id]
        bid = float(quotes.bid[event_id])
        ask = float(quotes.ask[event_id])
        liquidation = bid if intent.direction > 0 else ask
        signed_move = (liquidation - entry) * intent.direction
        if signed_move > best_signed:
            best_signed, mfe_id, mfe_price = signed_move, event_id, liquidation
        if signed_move < worst_signed:
            worst_signed, mae_id, mae_price = signed_move, event_id, liquidation

        if event_id > entry_id:
            gap_ms = _milliseconds(quote_time64, quotes.time[event_id - 1])
            max_gap_ms = max(max_gap_ms, gap_ms)
            if (config.max_interquote_gap_ms is not None
                    and gap_ms > config.max_interquote_gap_ms):
                return _failure(
                    intent, activation, config, "interquote_gap",
                    f"quote gap {gap_ms:.3f} ms exceeds {config.max_interquote_gap_ms:g} ms",
                    source_path,
                )

        exit_reason = None
        trigger = None
        eod_delay = None
        if quote_time64 >= eod64:
            eod_delay = _milliseconds(quote_time64, eod64)
            if config.max_eod_delay_ms is not None and eod_delay > config.max_eod_delay_ms:
                return _failure(
                    intent, activation, config, "eod_quote_delayed",
                    f"first valid EOD quote is {eod_delay:.3f} ms late; "
                    f"limit is {config.max_eod_delay_ms:g} ms", source_path,
                )
            exit_reason = "EOD"
        elif ((intent.direction > 0 and liquidation <= intent.stop_loss)
              or (intent.direction < 0 and liquidation >= intent.stop_loss)):
            exit_reason, trigger = "SL", intent.stop_loss
        elif ((intent.direction > 0 and liquidation >= target)
              or (intent.direction < 0 and liquidation <= target)):
            exit_reason, trigger = "TP", target

        if exit_reason is not None:
            exit_slippage = None if trigger is None else (
                (trigger - liquidation) * intent.direction / intent.point
            )
            return ExecutionOutcome(
                status="executed", detail=f"exited by {exit_reason}",
                direction=intent.direction, signal_time=intent.signal_time,
                order_activation_time=activation, source_path=source_path,
                entry_time=entry_time, entry_event_id=entry_id,
                entry_bid=entry_bid, entry_ask=entry_ask, entry=entry,
                entry_spread=spread, entry_delay_ms=entry_delay,
                reference_entry=intent.reference_entry,
                entry_slippage_points=reference_slippage,
                initial_sl=intent.stop_loss, final_sl=intent.stop_loss,
                target=target, risk_dist=risk,
                exit_time=pd.Timestamp(quote_time64), exit_event_id=event_id,
                exit_bid=bid, exit_ask=ask, exit=liquidation,
                exit_reason=exit_reason, exit_trigger_level=trigger,
                exit_slippage_points=exit_slippage, eod_delay_ms=eod_delay,
                r_multiple=signed_move / risk,
                mfe_r=max(best_signed, 0.0) / risk,
                mae_r=max(-worst_signed, 0.0) / risk,
                mfe_time=pd.Timestamp(quotes.time[mfe_id]), mfe_event_id=mfe_id,
                mfe_price=mfe_price,
                mae_time=pd.Timestamp(quotes.time[mae_id]), mae_event_id=mae_id,
                mae_price=mae_price,
                quote_events_processed=event_id - entry_id + 1,
                max_interquote_gap_ms=max_gap_ms,
            )

    return _failure(
        intent, activation, config, "quote_data_ended",
        "quote data ended before SL, TP, or EOD could execute", source_path,
    )


class NbboExecution:
    """Backend wrapper that lazily requests the correct WRDS source day."""

    def __init__(self, store: QuoteStore, config: NbboExecutionConfig | None = None) -> None:
        self.store = store
        self.config = config or NbboExecutionConfig()

    def execute(self, intent: ExecutionIntent, source_date: date | None = None) -> ExecutionOutcome:
        offset = pd.Timedelta(hours=self.store.strategy_offset_hours)
        inferred = (intent.activation_time - offset).date()
        day = source_date or inferred
        if (intent.eod_time - offset).date() != day:
            return _failure(
                intent, intent.activation_time + pd.to_timedelta(self.config.latency_ms, unit="ms"),
                self.config, "cross_day_not_supported",
                "NBBO milestone 1 requires activation and EOD in one WRDS source-date file",
            )
        try:
            quotes = self.store.load_day(day)
        except (FileNotFoundError, OSError, ValueError) as exc:
            return _failure(
                intent, intent.activation_time + pd.to_timedelta(self.config.latency_ms, unit="ms"),
                self.config, "missing_quote_day", str(exc),
            )
        return replay_nbbo(intent, quotes, self.config)


__all__ = [
    "ExecutionDataError", "ExecutionIntent", "ExecutionOutcome", "NbboExecution",
    "NbboExecutionConfig", "replay_nbbo",
]
