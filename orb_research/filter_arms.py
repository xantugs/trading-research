"""Prespecified multi-arm walk-forward test of two signal-time entry rules and a later exit.

Every arm keeps the frozen 08:20 / 60-minute / M5-close / opposite-range / 2R /
0.5% structure and the four annual folds of ``walk_forward``.  Inside each
training window only, an arm (1) takes the breakout buffer, (2) sets the
timing cutoff at a fixed percentile of ``minutes_since_or_end`` among the
completed training trades and (3) sets the range-width floor at a fixed
percentile of ``or_width_atr``.  Those values are frozen and the following
unseen year is run once.  Nothing is tuned against a test year.

Arms differ only in which rules are switched on, whether the session exit is
the base 16:00 or a later time with the *same* entry window, and whether the
buffer is the baseline's choice per fold (attribution) or re-selected after the
rules are applied (system).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

from .backtester import minimum_lot_feasibility, simulate_signals
from .config import Config
from .strategy import (Signal, entry_rules_pass, find_signals, minutes_since_range_end,
                       range_width_atr)
from .walk_forward import (CANDIDATE_BUFFERS, choose_buffer, filter_signals, fixed_folds,
                           performance, validate_walk_forward)


TIMING_PERCENTILE = 60.0  # keep first breakouts confirmed at or before this training percentile
WIDTH_PERCENTILE = 20.0  # keep sessions whose range / ATR14 is at or above this training percentile
EXIT_VARIANTS = ("17:00", "18:30")  # later exits keep the base entry window
BOOTSTRAP_DRAWS = 4000
BOOTSTRAP_SEED = 20260918


@dataclass(frozen=True)
class Arm:
    key: str
    early: bool = False
    width: bool = False
    session_exit: str | None = None  # None -> the base configuration's exit
    selection: str = "fixed"  # fixed: baseline buffer per fold | joint: re-select after the rules

    @property
    def label(self) -> str:
        rules = {(False, False): "no rules", (True, False): "early only",
                 (False, True): "OR-width only", (True, True): "early + OR-width"}[(self.early, self.width)]
        exit_label = f"exit {self.session_exit}" if self.session_exit else "exit 16:00"
        return f"{rules}, {exit_label}, {self.selection} buffer"


def prespecified_arms() -> tuple[Arm, ...]:
    arms = [Arm("base_1600_fixed")]
    for early, width in ((True, False), (False, True), (True, True)):
        arms.append(Arm(_key(early, width, None, "fixed"), early, width, None, "fixed"))
    for exit_time in EXIT_VARIANTS:
        arms.append(Arm(_key(False, False, exit_time, "fixed"), False, False, exit_time, "fixed"))
        for early, width in ((True, False), (False, True), (True, True)):
            arms.append(Arm(_key(early, width, exit_time, "fixed"), early, width, exit_time, "fixed"))
    for early, width in ((True, False), (False, True), (True, True)):
        arms.append(Arm(_key(early, width, None, "joint"), early, width, None, "joint"))
    return tuple(arms)


def _key(early: bool, width: bool, exit_time: str | None, selection: str) -> str:
    rules = {(False, False): "base", (True, False): "early", (False, True): "width",
             (True, True): "both"}[(early, width)]
    return f"{rules}_{(exit_time or '16:00').replace(':', '')}_{selection}"


def comparison_key(arm: Arm) -> str | None:
    """The arm each arm is paired against: same exit without rules, or the baseline."""
    if arm.key == "base_1600_fixed":
        return None
    if arm.selection == "joint" or (not arm.early and not arm.width):
        return "base_1600_fixed"
    return _key(False, False, arm.session_exit, "fixed")


def entry_window_minutes(base: Config) -> float:
    """Minutes from the end of the opening range to the base session exit."""
    open_minutes = _minutes(base.session_open)
    exit_minutes = _minutes(base.session_exit)
    return float(exit_minutes - open_minutes - base.range_minutes)


def _minutes(label: str) -> int:
    parsed = time.fromisoformat(label)
    return parsed.hour * 60 + parsed.minute


def training_cutoffs(signals_by_date: dict[str, Signal], trades: pd.DataFrame, window: float,
                     arm: Arm) -> tuple[float | None, float | None]:
    """Percentile rules from completed training trades inside the base entry window."""
    dates = [] if trades.empty else list(trades.session_date)
    eligible = [signals_by_date[date] for date in dates
                if minutes_since_range_end(signals_by_date[date]) <= window]
    if not eligible:
        raise ValueError("no eligible training trades for percentile rules")
    timing = np.array([minutes_since_range_end(signal) for signal in eligible])
    widths = np.array([range_width_atr(signal) for signal in eligible])
    cutoff = float(np.percentile(timing, TIMING_PERCENTILE)) if arm.early else None
    floor = float(np.percentile(widths, WIDTH_PERCENTILE)) if arm.width else None
    return cutoff, floor


def _effective_cutoff(cutoff: float | None, arm: Arm, window: float) -> float | None:
    """Later exits keep the base entry window; the early rule can only tighten it."""
    if arm.session_exit is None:
        return cutoff
    return window if cutoff is None else min(cutoff, window)


class _Scans:
    """Memoised unfiltered signal scans keyed by (prefix end, buffer, exit)."""

    def __init__(self, m1: pd.DataFrame, base: Config):
        self.m1 = m1
        self.base = base
        self._prefixes: dict[pd.Timestamp, pd.DataFrame] = {}
        self._scans: dict[tuple, tuple[list[Signal], dict]] = {}

    def prefix(self, end: pd.Timestamp) -> pd.DataFrame:
        if end not in self._prefixes:
            self._prefixes[end] = self.m1.loc[self.m1.index < end]
        return self._prefixes[end]

    def scan(self, end: pd.Timestamp, buffer: float, session_exit: str) -> tuple[list[Signal], dict]:
        key = (end, buffer, session_exit)
        if key not in self._scans:
            config = replace(self.base, breakout_buffer_atr=buffer, session_exit=session_exit)
            self._scans[key] = find_signals(self.prefix(end), config)
        return self._scans[key]


def run_arms(m1: pd.DataFrame, base: Config, arms: tuple[Arm, ...] | None = None, *,
             emit=print) -> dict:
    """Run every arm through the frozen folds; returns per-arm folds, OOS trades and summaries."""
    validate_walk_forward(base, m1)
    if base.entry_cutoff_minutes_after_or is not None or base.min_or_width_atr is not None:
        raise ValueError("the base configuration must not preset entry rules")
    arms = arms or prespecified_arms()
    window = entry_window_minutes(base)
    scans = _Scans(m1, base)
    folds = fixed_folds()

    # Baseline buffer per fold: highest unfiltered training mean R at the base exit.
    baseline_choice: dict[int, float] = {}
    for number, fold in enumerate(folds, start=1):
        train_end = pd.Timestamp(f"{fold.train_end}-01-01", tz="UTC")
        rows = []
        for buffer in CANDIDATE_BUFFERS:
            signals, _ = scans.scan(train_end, buffer, base.session_exit)
            selected = filter_signals(signals, fold.train_start, fold.train_end)
            trades, _ = simulate_signals(scans.prefix(train_end), selected,
                                         replace(base, breakout_buffer_atr=buffer))
            rows.append({"buffer": buffer, **performance(trades, base.initial_equity)})
        baseline_choice[number] = choose_buffer(pd.DataFrame(rows))
        emit(f"Fold {number}/4 baseline buffer {baseline_choice[number]:.2f}")

    results: dict[str, dict] = {}
    for arm in arms:
        emit(f"Arm {arm.key}: {arm.label}")
        exit_time = arm.session_exit or base.session_exit
        equity = base.initial_equity
        fold_rows, oos_parts, training_rows = [], [], []
        for number, fold in enumerate(folds, start=1):
            train_end = pd.Timestamp(f"{fold.train_end}-01-01", tz="UTC")
            test_end = pd.Timestamp(f"{fold.test_year + 1}-01-01", tz="UTC")
            candidates = CANDIDATE_BUFFERS if arm.selection == "joint" else (baseline_choice[number],)
            evaluated = []
            for buffer in candidates:
                config = replace(base, breakout_buffer_atr=buffer, session_exit=exit_time)
                signals, _ = scans.scan(train_end, buffer, exit_time)
                selected = filter_signals(signals, fold.train_start, fold.train_end)
                unfiltered, _ = simulate_signals(scans.prefix(train_end), selected, config)
                by_date = {signal.session_date: signal for signal in selected}
                cutoff, floor = training_cutoffs(by_date, unfiltered, window, arm)
                effective = _effective_cutoff(cutoff, arm, window)
                kept = [signal for signal in selected if entry_rules_pass(signal, effective, floor)]
                trades, execution = simulate_signals(scans.prefix(train_end), kept, config)
                result = performance(trades, base.initial_equity)
                train_feasibility = minimum_lot_feasibility(scans.prefix(train_end), kept, config)
                evaluated.append({"fold": number, "buffer": buffer, "cutoff_minutes": cutoff,
                                  "effective_cutoff_minutes": effective, "min_width_atr": floor,
                                  "unfiltered_trades": len(unfiltered),
                                  "train_5k_infeasible_share": train_feasibility["infeasible_share"],
                                  "train_5k_infeasible_signals": train_feasibility["infeasible_at_5k"],
                                  **result})
            training_rows.extend(evaluated)
            chosen = (choose_buffer(pd.DataFrame(evaluated)) if arm.selection == "joint"
                      else float(candidates[0]))
            row = next(item for item in evaluated if item["buffer"] == chosen)
            config = replace(base, breakout_buffer_atr=chosen, session_exit=exit_time,
                             entry_cutoff_minutes_after_or=row["effective_cutoff_minutes"],
                             min_or_width_atr=row["min_width_atr"])
            config.validate()
            test_prefix = scans.prefix(test_end)
            signals, scan_counts = find_signals(test_prefix, config)  # the engine path, rules inside
            selected = filter_signals(signals, fold.test_year, fold.test_year + 1)
            # The engine path must agree with post-filtering the unfiltered scan.
            reference, _ = scans.scan(test_end, chosen, exit_time)
            expected = [signal.session_date for signal in filter_signals(reference, fold.test_year,
                                                                          fold.test_year + 1)
                        if entry_rules_pass(signal, row["effective_cutoff_minutes"],
                                            row["min_width_atr"])]
            if [signal.session_date for signal in selected] != expected:
                raise AssertionError(f"{arm.key} fold {number}: engine rules and post-filter disagree")
            starting_equity = equity
            trades, execution = simulate_signals(test_prefix, selected, config,
                                                 initial_equity=starting_equity)
            feasibility = minimum_lot_feasibility(test_prefix, selected, config)
            result = performance(trades, starting_equity)
            equity = result["end_equity"]
            if not trades.empty:
                trades = trades.copy()
                by_date = {signal.session_date: signal for signal in selected}
                trades["arm"] = arm.key
                trades["walk_forward_fold"] = number
                trades["selected_buffer"] = chosen
                trades["minutes_since_or_end"] = trades.session_date.map(
                    lambda date: minutes_since_range_end(by_date[date]))
                trades["or_width_atr"] = trades.session_date.map(
                    lambda date: range_width_atr(by_date[date]))
                oos_parts.append(trades)
            fold_rows.append({
                "arm": arm.key, "fold": number, "train_start": fold.train_start,
                "train_end_inclusive": fold.train_end - 1, "test_year": fold.test_year,
                "selected_buffer": chosen, "selected_train_mean_r": row["mean_r"],
                "cutoff_minutes": row["effective_cutoff_minutes"], "min_width_atr": row["min_width_atr"],
                "test_start_equity": starting_equity, **result,
                "test_signals": execution["signals"], "test_skipped": execution["skipped"],
                "test_5k_valid_sizing_signals": feasibility["valid_sizing_signals"],
                "test_5k_infeasible_signals": feasibility["infeasible_at_5k"],
                "test_5k_infeasible_share": feasibility["infeasible_share"],
                "late_breakout_sessions": scan_counts["late_breakout"],
                "narrow_range_sessions": scan_counts["narrow_range"],
            })
            emit(f"  {fold.test_year}: buffer {chosen:.2f} cutoff {row['effective_cutoff_minutes']} "
                 f"width {row['min_width_atr']} -> {result['trades']} trades, "
                 f"mean R {result['mean_r'] if result['mean_r'] is None else round(result['mean_r'], 4)}")
        oos = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
        results[arm.key] = {"arm": arm, "folds": pd.DataFrame(fold_rows), "oos": oos,
                            "training": pd.DataFrame(training_rows),
                            "combined": performance(oos, base.initial_equity)}
    results_paired = paired_comparisons(results)
    return {"arms": results, "paired": results_paired, "baseline_choice": baseline_choice,
            "window_minutes": window}


def _session_r(oos: pd.DataFrame) -> pd.Series:
    if oos.empty:
        return pd.Series(dtype=float)
    return oos.set_index("session_date").R_result.astype(float)


def paired_comparisons(results: dict[str, dict]) -> pd.DataFrame:
    """Expectancy difference against the comparison arm with a session-block bootstrap."""
    rows = []
    for key, item in results.items():
        other = comparison_key(item["arm"])
        if other is None or other not in results:
            continue
        x = _session_r(item["oos"])
        c = _session_r(results[other]["oos"])
        sessions = sorted(set(x.index) | set(c.index))
        removed = c.loc[~c.index.isin(x.index)]
        added = x.loc[~x.index.isin(c.index)]
        common = x.index.intersection(c.index)
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        x_values = x.reindex(sessions).to_numpy()
        c_values = c.reindex(sessions).to_numpy()
        diffs = np.empty(BOOTSTRAP_DRAWS)
        n = len(sessions)
        for draw in range(BOOTSTRAP_DRAWS):
            pick = rng.integers(0, n, n)
            xs, cs = x_values[pick], c_values[pick]
            diffs[draw] = np.nanmean(xs) - np.nanmean(cs)
        observed = float(x.mean() - c.mean())
        rows.append({
            "arm": key, "versus": other, "trades": len(x), "versus_trades": len(c),
            "mean_r": float(x.mean()), "versus_mean_r": float(c.mean()),
            "expectancy_difference": observed,
            "removed_sessions": len(removed), "removed_mean_r": float(removed.mean()) if len(removed) else None,
            "added_sessions": len(added), "added_mean_r": float(added.mean()) if len(added) else None,
            "common_sessions": len(common),
            "common_paired_mean_diff": float((x.loc[common] - c.loc[common]).mean()) if len(common) else None,
            "bootstrap_mean": float(np.nanmean(diffs)),
            "ci_low": float(np.nanpercentile(diffs, 2.5)),
            "ci_high": float(np.nanpercentile(diffs, 97.5)),
            "p_le_zero": float(np.nanmean(diffs <= 0)),
        })
    return pd.DataFrame(rows)


def summary_table(results: dict) -> pd.DataFrame:
    rows = []
    for key, item in results["arms"].items():
        arm, combined, folds = item["arm"], item["combined"], item["folds"]
        rows.append({
            "arm": key, "label": arm.label, "early": arm.early, "width": arm.width,
            "session_exit": arm.session_exit or "16:00", "selection": arm.selection,
            "trades": combined["trades"], "mean_r": combined["mean_r"],
            "profit_factor": combined["profit_factor"], "win_rate": combined["win_rate"],
            "return_pct": combined["return_pct"], "max_closed_dd_pct": combined["max_closed_dd_pct"],
            "time_exit_fraction": combined["time_exit_fraction"],
            "years_positive": int((folds.return_pct > 0).sum()),
            "buffers": "/".join(f"{b:.2f}" for b in folds.selected_buffer),
        })
    return pd.DataFrame(rows)


def write_results(output: Path, results: dict, base: Config, provenance: dict,
                  baseline_check: dict | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    (output / "oos_trades").mkdir()
    summary = summary_table(results)
    summary.to_csv(output / "arms_summary.csv", index=False)
    folds = pd.concat([item["folds"] for item in results["arms"].values()], ignore_index=True)
    folds["test_skipped"] = folds.test_skipped.map(json.dumps)
    folds.to_csv(output / "arms_folds.csv", index=False)
    training = pd.concat([item["training"].assign(arm=key)
                          for key, item in results["arms"].items()], ignore_index=True)
    training.to_csv(output / "arms_training.csv", index=False)
    results["paired"].to_csv(output / "arms_paired.csv", index=False)
    for key, item in results["arms"].items():
        item["oos"].to_csv(output / "oos_trades" / f"{key}.csv", index=False)
    spec = {
        "purpose": "development-period multi-arm test of prespecified entry rules and later exits",
        "timing_percentile": TIMING_PERCENTILE, "width_percentile": WIDTH_PERCENTILE,
        "exit_variants": list(EXIT_VARIANTS), "entry_window_minutes": results["window_minutes"],
        "arms": [asdict(item["arm"]) for item in results["arms"].values()],
        "comparisons": {key: comparison_key(item["arm"]) for key, item in results["arms"].items()},
        "baseline_buffer_by_fold": results["baseline_choice"],
        "candidate_buffers": list(CANDIDATE_BUFFERS), "folds": [asdict(fold) for fold in fixed_folds()],
        "selection": ("fixed arms reuse the baseline buffer per fold; joint arms pick the highest "
                      "training mean net R after the rules; smaller buffer breaks exact ties"),
        "cutoff_rule": ("percentiles over completed training trades at the candidate buffer whose "
                        "first breakout fell inside the base entry window; comparisons are <= for "
                        "timing and >= for width"),
        "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
                      "unit": "session_date", "statistic": "mean R difference vs comparison arm"},
        "holdout_used": False, "holdout_start": base.holdout_start,
        "base_config": asdict(base), "data_provenance": provenance,
        "baseline_check": baseline_check,
        "code_sha256": {
            name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
            for name in ("strategy.py", "backtester.py", "walk_forward.py", "filter_arms.py")
        },
    }
    (output / "spec.json").write_text(json.dumps(spec, indent=2, default=str) + "\n", encoding="utf-8")
    report = output / "report.md"
    report.write_text(report_markdown(results, base, baseline_check), encoding="utf-8")
    return report


def _fmt(value, spec: str = "+.4f", none: str = "—") -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return none
    return format(value, spec)


def report_markdown(results: dict, base: Config, baseline_check: dict | None) -> str:
    summary = summary_table(results)
    paired = results["paired"].set_index("arm") if not results["paired"].empty else None
    lines = ["# XAUUSD ORB — multi-arm entry-rule and exit walk-forward", "",
             "Development data only (2018–2024); the 2025-onward holdout was not read. "
             "Folds, buffer candidates and selection rule as in `walk_forward.py`; the baseline chose "
             + ", ".join(f"{b:.2f}" for b in results["baseline_choice"].values()) + " for 2021–2024.", "",
             f"Rules are set inside each training window only: timing cutoff = P{TIMING_PERCENTILE:.0f} of "
             "minutes from range end to the confirming M5 close, range floor = "
             f"P{WIDTH_PERCENTILE:.0f} of range width / ATR14, both over completed training trades at the "
             f"chosen buffer. Later exits keep the base entry window ({results['window_minutes']:.0f} min "
             "after the range).", ""]
    if baseline_check:
        lines += [f"Baseline arm vs `walk_forward.run_walk_forward`: {baseline_check['status']} "
                  f"({baseline_check['arm_trades']} vs {baseline_check['walk_forward_trades']} trades, "
                  f"mean R {baseline_check['arm_mean_r']:+.4f} vs {baseline_check['walk_forward_mean_r']:+.4f}).", ""]
    lines += ["## Concatenated unseen 2021–2024 by arm", "",
              "| Arm | Rules | Exit | Buffer | Trades | Mean R | PF | Win | Return | Max DD | Time exits | Years > 0 | Δ mean R vs comparison | 95% CI | P(Δ ≤ 0) |",
              "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |"]
    for row in summary.itertuples(index=False):
        rules = {(False, False): "none", (True, False): "early", (False, True): "width",
                 (True, True): "both"}[(row.early, row.width)]
        pair = paired.loc[row.arm] if paired is not None and row.arm in paired.index else None
        lines.append(
            f"| `{row.arm}` | {rules} | {row.session_exit} | {row.selection} ({row.buffers}) | {row.trades} | "
            f"{_fmt(row.mean_r)} | {_fmt(row.profit_factor, '.3f')} | {_fmt(row.win_rate, '.1%')} | "
            f"{_fmt(row.return_pct, '+.2f')}% | {_fmt(row.max_closed_dd_pct, '.2f')}% | "
            f"{_fmt(row.time_exit_fraction, '.0%')} | {row.years_positive}/4 | "
            + (f"{pair.expectancy_difference:+.4f} vs `{pair.versus}` | [{pair.ci_low:+.3f}, {pair.ci_high:+.3f}] | {pair.p_le_zero:.3f} |"
               if pair is not None else "— | — | — |"))
    lines += ["", "## Fold by fold (mean R, trades)", ""]
    years = [fold.test_year for fold in fixed_folds()]
    lines.append("| Arm | " + " | ".join(str(year) for year in years) + " | Cutoffs (min) | Width floors (ATR) |")
    lines.append("| --- | " + " | ".join("---:" for _ in years) + " | --- | --- |")
    for key, item in results["arms"].items():
        folds = item["folds"]
        cells = [f"{_fmt(r.mean_r, '+.3f')} ({r.trades})" for r in folds.itertuples(index=False)]
        cutoffs = "/".join(_fmt(c, '.0f', '—') for c in folds.cutoff_minutes)
        floors = "/".join(_fmt(w, '.3f', '—') for w in folds.min_width_atr)
        lines.append(f"| `{key}` | " + " | ".join(cells) + f" | {cutoffs} | {floors} |")
    lines += ["", "## Paired view against the comparison arm", ""]
    if paired is not None:
        lines += ["| Arm | vs | Removed sessions | Removed mean R | Added sessions | Added mean R | Common sessions | Paired Δ on common | Bootstrap Δ mean | 95% CI | P(Δ ≤ 0) |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |"]
        for key, row in paired.iterrows():
            lines.append(f"| `{key}` | `{row.versus}` | {row.removed_sessions} | {_fmt(row.removed_mean_r)} | "
                         f"{row.added_sessions} | {_fmt(row.added_mean_r)} | {row.common_sessions} | "
                         f"{_fmt(row.common_paired_mean_diff)} | {row.bootstrap_mean:+.4f} | "
                         f"[{row.ci_low:+.3f}, {row.ci_high:+.3f}] | {row.p_le_zero:.3f} |")
    lines += ["", "## Reading rules", "",
              "The two entry rules were shortlisted from a descriptive analysis of the same 2018–2024 "
              "history, so consistency across these folds shows that the percentile rules transfer, not "
              "that the hypotheses generalise; independent evidence needs another instrument or session, "
              "or the sealed holdout after a complete freeze. Per-fold samples are small (tens of "
              "trades); read the paired bootstrap rather than fold signs. Results are M1-bar simulations "
              "with modeled costs and stop-first ambiguity, not tick-exact fills.", ""]
    return "\n".join(lines)
