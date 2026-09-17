"""Illustrative closed-trade prop-rule replay; rules supplied by configuration."""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo
from pathlib import Path
import json

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PropRules:
    account_size: float = 5000.0
    phase1_target_pct: float = 10.0
    phase2_target_pct: float = 5.0
    daily_loss_limit_pct: float = 5.0
    max_loss_limit_pct: float = 10.0
    min_profitable_days: int = 0
    reset_timezone: str = "UTC"

    def validate(self) -> None:
        ZoneInfo(self.reset_timezone)
        if self.account_size <= 0:
            raise ValueError("account_size must be positive")
        if min(self.phase1_target_pct, self.phase2_target_pct,
               self.daily_loss_limit_pct, self.max_loss_limit_pct) <= 0:
            raise ValueError("targets and loss limits must be positive")
        if self.min_profitable_days < 0:
            raise ValueError("min_profitable_days must be nonnegative")


def load_prop_rules(path: str | Path) -> PropRules:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("prop rules must be a JSON object")
    allowed = set(PropRules.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown prop-rule fields: {', '.join(sorted(unknown))}")
    rules = PropRules(**raw)
    rules.validate()
    return rules


def _replay_phase(ordered: pd.DataFrame, start: int, risk_fraction: float, rules: PropRules,
                  target_pct: float):
    equity = rules.account_size
    initial = equity
    last_day = None
    day_start = equity
    day_pnl = 0.0
    profitable_days = 0
    trades_used = 0
    first_time = ordered.iloc[start].exit_time
    for index in range(start, len(ordered)):
        trade = ordered.iloc[index]
        day = trade.exit_time.tz_convert(rules.reset_timezone).date()
        if last_day is not None and day != last_day:
            profitable_days += day_pnl > 0
            day_pnl = 0.0
            day_start = equity
        last_day = day
        change = equity * risk_fraction * float(trade.R_result)
        equity += change
        day_pnl += change
        trades_used += 1
        if equity <= day_start * (1 - rules.daily_loss_limit_pct / 100):
            return "daily_dd", index + 1, trades_used, (trade.exit_time - first_time).days
        if equity <= initial * (1 - rules.max_loss_limit_pct / 100):
            return "total_dd", index + 1, trades_used, (trade.exit_time - first_time).days
        completed_days = profitable_days + int(day_pnl > 0)
        if equity >= initial * (1 + target_pct / 100) and completed_days >= rules.min_profitable_days:
            return "passed", index + 1, trades_used, (trade.exit_time - first_time).days
    return "censored", len(ordered), trades_used, (ordered.iloc[-1].exit_time - first_time).days


def simulate_prop(trades: pd.DataFrame, rules: PropRules = PropRules(),
                  risk_levels=(0.25, 0.5, 0.75, 1.0)) -> pd.DataFrame:
    rules.validate()
    if trades.empty:
        raise ValueError("no trades to replay")
    ordered = trades.sort_values("exit_time").reset_index(drop=True)
    if not np.isfinite(ordered.R_result.to_numpy(dtype=float)).all():
        raise ValueError("non-finite R result")
    # Each distinct starting date contributes one scenario; recent starts may
    # finish as censored and must not be called failures or successes.
    days = ordered.exit_time.dt.tz_convert(rules.reset_timezone).dt.date
    starts = ordered.groupby(days, sort=True).head(1).index.to_numpy()
    records = []
    for risk_pct in risk_levels:
        if not 0 < risk_pct <= 2:
            raise ValueError("risk level must be in (0, 2]")
        for start in starts:
            phase1, next_index, used1, days1 = _replay_phase(
                ordered, int(start), risk_pct / 100, rules, rules.phase1_target_pct)
            if phase1 == "passed" and next_index < len(ordered):
                phase2, _, used2, days2 = _replay_phase(
                    ordered, next_index, risk_pct / 100, rules, rules.phase2_target_pct)
            else:
                phase2, used2, days2 = ("not_started" if phase1 != "passed" else "censored"), 0, 0
            records.append({
                "risk_percent": risk_pct, "start_date": str(days.iloc[start]),
                "phase1": phase1, "phase2": phase2,
                "complete_pass": phase1 == "passed" and phase2 == "passed",
                "trades_to_result": used1 + used2,
                "calendar_days_to_result": days1 + days2,
                "failure_reason": (phase1 if phase1 in ("daily_dd", "total_dd")
                                   else phase2 if phase2 in ("daily_dd", "total_dd") else ""),
            })
    return pd.DataFrame.from_records(records)


def summarize_prop(results: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for risk, group in results.groupby("risk_percent"):
        phase1_resolved = group.phase1.isin(("passed", "daily_dd", "total_dd"))
        phase2_entered = group.loc[group.phase1 == "passed"]
        phase2_resolved = phase2_entered.phase2.isin(("passed", "daily_dd", "total_dd"))
        completed = group.loc[group.complete_pass]
        rows.append({
            "risk_percent": risk, "starting_dates": len(group),
            "phase1_pass_fraction_resolved": (
                float((group.loc[phase1_resolved].phase1 == "passed").mean())
                if phase1_resolved.any() else np.nan),
            "phase2_pass_fraction_given_phase1_resolved": (
                float((phase2_entered.loc[phase2_resolved].phase2 == "passed").mean())
                if phase2_resolved.any() else np.nan),
            "complete_pass_fraction_all_starts_lower_bound": float(group.complete_pass.mean()),
            "censored_fraction": float(((group.phase1 == "censored") |
                                        (group.phase2 == "censored")).mean()),
            "median_days_to_complete_pass": (float(completed.calendar_days_to_result.median())
                                             if len(completed) else np.nan),
            "median_trades_to_complete_pass": (float(completed.trades_to_result.median())
                                               if len(completed) else np.nan),
            "daily_dd_failures": int((group.failure_reason == "daily_dd").sum()),
            "total_dd_failures": int((group.failure_reason == "total_dd").sum()),
        })
    return pd.DataFrame(rows)
