"""Build and audit the event-level table from the unchanged ORB engine."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .backtester import simulate_signals
from .config import Config
from .ml_features import FEATURE_COLUMNS, FeatureContext
from .strategy import Signal, find_signals, prior_daily_atr
from .walk_forward import CANDIDATE_BUFFERS, fixed_folds, validate_walk_forward


IDENTIFIER_COLUMNS = (
    "event_id", "session_id", "date", "buffer", "direction", "year", "wf_fold_role",
    "signal_time_utc", "entry_time_utc", "signal_time_ny", "entry_time_ny",
)
EXECUTION_COLUMNS = (
    "or_start_utc", "or_end_utc", "entry_price", "stop_price", "target_price", "exit_price",
    "exit_time_utc", "ambiguous_m1",
)
LABEL_COLUMNS = (
    "exit_reason", "final_R", "net_pnl", "MAE_R", "MFE_R", "trade_positive",
    "reached_0_5R", "reached_1R", "reached_2R", "stopped_out", "session_exit",
)
ALL_COLUMNS = IDENTIFIER_COLUMNS + FEATURE_COLUMNS + EXECUTION_COLUMNS + LABEL_COLUMNS


def _roles(year: int) -> str:
    roles = []
    for number, fold in enumerate(fixed_folds(), 1):
        if fold.train_start <= year < fold.train_end:
            roles.append(f"train_{number}")
        if year == fold.test_year:
            roles.append(f"test_{number}")
    return "|".join(roles) or "none"


def build_event_dataset(m1: pd.DataFrame, base: Config, *, emit=print) -> tuple[pd.DataFrame, dict]:
    """One completed candidate trade per row; all buffers keep their own entry snapshot."""
    validate_walk_forward(base, m1)
    context = FeatureContext(m1, base.signal_minutes)
    rows = []
    details = {}
    for buffer in CANDIDATE_BUFFERS:
        config = replace(base, breakout_buffer_atr=buffer)
        signals, scan = find_signals(m1, config)
        trades, execution = simulate_signals(m1, signals, config)
        by_session: dict[str, Signal] = {s.session_date: s for s in signals}
        if len(by_session) != len(signals):
            raise AssertionError("strategy returned multiple signals for one session")
        for trade in trades.itertuples(index=False):
            signal = by_session[trade.session_date]
            if trade.signal_close != signal.signal_close or trade.timestamp != signal.signal_close:
                raise AssertionError("executed trade does not match its signal or next M1 entry")
            features, sources = context.snapshot(signal, buffer)
            session_id = f"{base.symbol}|{base.session_name}|{signal.session_date}"
            event_id = f"{session_id}|b{round(buffer * 100):02d}"
            year = int(signal.session_date[:4])
            row = {
                "event_id": event_id, "session_id": session_id, "date": signal.session_date,
                "buffer": buffer, "direction": trade.direction, "year": year,
                "wf_fold_role": _roles(year),
                "signal_time_utc": signal.signal_close, "entry_time_utc": trade.timestamp,
                "signal_time_ny": signal.signal_close.tz_convert(base.session_timezone),
                "entry_time_ny": trade.timestamp.tz_convert(base.session_timezone),
                **features,
                "or_start_utc": signal.range_start, "or_end_utc": signal.range_end,
                "entry_price": trade.entry, "stop_price": trade.stop,
                "target_price": trade.take_profit, "exit_price": trade.exit,
                "exit_time_utc": trade.exit_time, "ambiguous_m1": bool(trade.ambiguous_m1),
                "exit_reason": trade.exit_reason, "final_R": trade.R_result,
                "net_pnl": trade.PnL, "MAE_R": trade.MAE, "MFE_R": trade.MFE,
                "trade_positive": int(trade.R_result > 0),
                "reached_0_5R": int(trade.MFE >= 0.5),
                "reached_1R": int(trade.MFE >= 1),
                "reached_2R": int(trade.MFE >= 2),
                "stopped_out": int(str(trade.exit_reason).startswith("stop")),
                "session_exit": int(trade.exit_reason == "time"),
            }
            if set(row) != set(ALL_COLUMNS):
                raise AssertionError("dataset schema mismatch")
            if any(ts > signal.signal_close for ts in sources.values()):
                raise AssertionError("future feature source")
            rows.append(row)
        details[f"{buffer:.2f}"] = {"signals": len(signals), "trades": len(trades),
                                     "skipped": execution["skipped"], "scan": scan}
        emit(f"Buffer {buffer:.2f}: {len(trades):,} completed candidates from {len(signals):,} signals")
    frame = pd.DataFrame.from_records(rows, columns=ALL_COLUMNS)
    if frame.empty:
        raise ValueError("no candidate trades in the development period")
    frame = frame.sort_values(["signal_time_utc", "buffer"], kind="stable").reset_index(drop=True)
    return frame, details


def audit_event_dataset(frame: pd.DataFrame, m1: pd.DataFrame, base: Config) -> dict:
    """Recompute sampled pre-entry snapshots from truncated raw history."""
    validate_walk_forward(base, m1)
    errors = []
    if frame.event_id.duplicated().any():
        errors.append("duplicate event_id")
    if (frame.signal_time_utc != frame.entry_time_utc).any():
        errors.append("signal and entry timestamps differ from the existing M1-open execution design")
    if (frame.signal_time_utc >= pd.Timestamp("2025-01-01", tz="UTC")).any():
        errors.append("2025+ event")
    if set(FEATURE_COLUMNS) & set(LABEL_COLUMNS + EXECUTION_COLUMNS):
        errors.append("predictor overlaps outcome or execution fields")
    forbidden = ("exit", "future", "mae", "mfe", "final_r", "pnl")
    contaminated = [name for name in FEATURE_COLUMNS if any(term in name.lower() for term in forbidden)]
    if contaminated:
        errors.append(f"feature names contain outcome concepts: {contaminated}")

    # This checks that an already-built full-history feature context cannot see data
    # beyond a sampled event: its values must equal a fresh context ending at the
    # entry boundary. The strategy's shifted prior UTC-day ATR is separately reused.
    full = FeatureContext(m1, base.signal_minutes)
    random = frame.sample(n=min(20, len(frame)), random_state=base.random_seed)
    recomputed = 0
    source_timestamp_checks = 0
    for row in random.itertuples(index=False):
        prefix = m1.loc[m1.index < row.entry_time_utc]
        signal = Signal(row.date, base.session_name, row.direction_sign,
                        row.or_start_utc, row.or_end_utc, row.or_high, row.or_low,
                        row.signal_time_utc - pd.Timedelta(minutes=base.signal_minutes),
                        row.signal_time_utc, float(prefix.loc[row.signal_time_utc - pd.Timedelta(minutes=1), "close"]),
                        row.atr_at_entry)
        try:
            whole, whole_sources = full.snapshot(signal, row.buffer)
            past, past_sources = FeatureContext(prefix, base.signal_minutes).snapshot(signal, row.buffer)
        except (ValueError, KeyError, IndexError) as exc:
            errors.append(f"snapshot {row.event_id}: {exc}")
            continue
        historical_atr = float(prior_daily_atr(prefix).get(row.or_start_utc.floor("D"), np.nan))
        if not np.isclose(historical_atr, row.atr_at_entry, rtol=1e-10, atol=1e-10):
            errors.append(f"{row.event_id}: ATR differs from completed-day prefix recomputation")
        for name in FEATURE_COLUMNS:
            expected = getattr(row, name)
            for label, actual in (("full", whole[name]), ("prefix", past[name])):
                if not (pd.isna(expected) and pd.isna(actual)) and not np.isclose(expected, actual, rtol=1e-10, atol=1e-10):
                    errors.append(f"{row.event_id} {name}: CSV vs {label} prefix mismatch")
            if whole_sources[name] > row.signal_time_utc or past_sources[name] > row.signal_time_utc:
                errors.append(f"{row.event_id} {name}: post-signal source timestamp")
            source_timestamp_checks += 1
        recomputed += 1

    multi = frame.groupby("session_id").filter(lambda group: len(group) > 1)
    groups = []
    for session_id, group in multi.groupby("session_id", sort=True):
        if group.signal_time_utc.nunique() > 1:
            groups.append((session_id, group))
    if len(groups) < 10:
        errors.append(f"only {len(groups)} sessions have multiple distinct buffer entry times; need 10")
    rng = np.random.default_rng(base.random_seed)
    indices = rng.choice(len(groups), size=min(10, len(groups)), replace=False) if groups else []
    checked_multi = 0
    for index in indices:
        session_id, group = groups[int(index)]
        if group.session_id.nunique() != 1 or group.event_id.nunique() != len(group):
            errors.append(f"{session_id}: invalid multi-buffer identity")
        for row in group.itertuples(index=False):
            prefix = m1.loc[m1.index < row.entry_time_utc]
            signal = Signal(row.date, base.session_name, row.direction_sign,
                            row.or_start_utc, row.or_end_utc, row.or_high, row.or_low,
                            row.signal_time_utc - pd.Timedelta(minutes=base.signal_minutes),
                            row.signal_time_utc, float(prefix.loc[row.signal_time_utc - pd.Timedelta(minutes=1), "close"]),
                            row.atr_at_entry)
            own, sources = full.snapshot(signal, row.buffer)
            for name in FEATURE_COLUMNS:
                expected = getattr(row, name)
                if not (pd.isna(expected) and pd.isna(own[name])) and not np.isclose(expected, own[name], rtol=1e-10, atol=1e-10):
                    errors.append(f"{row.event_id} {name}: multi-buffer own-snapshot mismatch")
                if sources[name] > row.signal_time_utc:
                    errors.append(f"{row.event_id} {name}: multi-buffer post-signal source")
        checked_multi += 1
    return {
        "passed": not errors, "holdout_used": False, "max_market_timestamp_utc": str(m1.index.max()),
        "event_rows": len(frame), "random_prefix_recomputations": recomputed,
        "feature_source_timestamp_checks": source_timestamp_checks,
        "multi_buffer_sessions_with_distinct_entry_times": len(groups),
        "multi_buffer_own_snapshot_sessions_checked": checked_multi,
        "timestamp_boundary": "M5 signal close equals next M1 entry bar open; feature M1 bars end one minute earlier",
        "errors": errors[:100], "error_count": len(errors),
    }
