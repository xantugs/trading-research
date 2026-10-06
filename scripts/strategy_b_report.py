"""Summarise the prespecified true-New-York ORB test without reading market data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


ARMS = ("base_1600_fixed", "both_1600_fixed")
LEGACY_WF = Path("outputs/walk_forward/20260917T170501Z-bfb09f")
LEGACY_ARMS = Path("outputs/filter_arms/20260918T051500Z-cloud1")


def _fmt(value, digits=3):
    return "—" if pd.isna(value) else f"{value:+.{digits}f}"


def _extra(trades: pd.DataFrame, folds: pd.DataFrame, start_equity: float = 5000.0) -> dict:
    if trades.empty:
        return {"median_r": None, "long_expectancy_r": None, "short_expectancy_r": None,
                "profitable_years": 0, "yearly_returns_pct": {}, "longest_losing_streak": 0,
                "max_closed_drawdown_duration_days": 0.0}
    ordered = trades.sort_values("exit_time").reset_index(drop=True)
    r = ordered.R_result.astype(float)
    equity = np.r_[start_equity, ordered.equity_after.to_numpy(dtype=float)]
    times = [pd.Timestamp("2021-01-01", tz="UTC")] + list(pd.to_datetime(ordered.exit_time, utc=True))
    max_duration = 0.0
    high, high_time = equity[0], times[0]
    for balance, stamp in zip(equity[1:], times[1:]):
        if balance >= high:
            high, high_time = balance, stamp
        else:
            max_duration = max(max_duration, (stamp - high_time).total_seconds() / 86400)
    losing = (r < 0).to_numpy()
    streak = longest = 0
    for flag in losing:
        streak = streak + 1 if flag else 0
        longest = max(longest, streak)
    by_direction = ordered.groupby("direction").R_result.mean().to_dict()
    return {"median_r": float(r.median()),
            "long_expectancy_r": by_direction.get("long"),
            "short_expectancy_r": by_direction.get("short"),
            "profitable_years": int((folds.return_pct > 0).sum()),
            "yearly_returns_pct": {str(int(row.test_year)): float(row.return_pct)
                                   for row in folds.itertuples(index=False)},
            "longest_losing_streak": longest,
            "max_closed_drawdown_duration_days": max_duration}


def render(results_dir: Path, output_dir: Path, *, a_reproduction: Path | None = None) -> dict:
    spec = json.loads((results_dir / "spec.json").read_text(encoding="utf-8"))
    config = spec["base_config"]
    if config["broker_clock"] != "new_york_plus_7" or spec["holdout_used"]:
        raise ValueError("Strategy B report requires corrected clock and sealed holdout")
    summaries = pd.read_csv(results_dir / "arms_summary.csv").set_index("arm")
    folds = pd.read_csv(results_dir / "arms_folds.csv")
    training = pd.read_csv(results_dir / "arms_training.csv")
    paired = pd.read_csv(results_dir / "arms_paired.csv").set_index("arm")
    trades = {arm: pd.read_csv(results_dir / "oos_trades" / f"{arm}.csv") for arm in ARMS}
    if any(not data.empty and (data.session_date >= "2025-01-01").any() for data in trades.values()):
        raise ValueError("final holdout reached Strategy B report")
    baseline, filtered = trades[ARMS[0]], trades[ARMS[1]]
    kept = baseline.loc[baseline.session_date.isin(filtered.session_date)]
    removed = baseline.loc[~baseline.session_date.isin(filtered.session_date)]
    if len(kept) != len(filtered):
        raise ValueError("fixed-buffer B2 must be a subset of B1 sessions")
    legacy_spec = json.loads((LEGACY_WF / "spec.json").read_text(encoding="utf-8"))
    if legacy_spec["base_config"].get("broker_clock", "stored_as_utc") != "stored_as_utc":
        raise ValueError("Strategy A reference is not the legacy clock")
    a = json.loads((LEGACY_WF / "summary.json").read_text(encoding="utf-8"))
    a_arms = pd.read_csv(LEGACY_ARMS / "arms_summary.csv").set_index("arm")
    a_check = "not rerun in this command"
    if a_reproduction is not None:
        fresh = json.loads((a_reproduction / "summary.json").read_text(encoding="utf-8"))
        same = (fresh["trades"] == a["trades"] and
                abs(fresh["mean_r"] - a["mean_r"]) < 1e-12 and
                abs(fresh["return_pct"] - a["return_pct"]) < 1e-9)
        a_check = "PASS" if same else "FAIL"
        if not same:
            raise ValueError("Strategy A legacy reproduction changed")
    clock = json.loads((output_dir / "clock_verification.json").read_text(encoding="utf-8"))
    if not clock["pass"]:
        raise ValueError("clock verification did not pass")
    lines = ["# Strategy B — true 08:20 New York XAUUSD ORB", "",
             "Clock mapping: **PASS**. Six 08:30 Eastern BLS releases (winter, summer, and DST-mismatch weeks) produced stored-clock activity bursts at 15:30–15:34; decoded trading weeks open Sunday evening and close late Friday New York time. See [clock verification](clock_verification.json) and the BLS [2023](https://www.bls.gov/schedule/2023/home.htm) and [2024](https://www.bls.gov/schedule/2024/) release calendars.",
             "", "The raw MT5 cache is preserved. Strategy A keeps its legacy stored-clock interpretation. Strategy B decodes FTMO server wall time as New York + 7 hours, then constructs an actual 08:20–09:20 New York range and 16:00 New York exit. The 2025+ holdout was not loaded.", "",
             "Both variants use M5 close confirmation, first breakout only, opposite-range stop, 2R nominal target, 0.5% research risk, and the same modeled spread, commission, slippage and M1 stop-first execution. The four buffer candidates are 0.25/0.35/0.45/0.55 ATR. B2 applies train-only P60 timing and P20 OR-width thresholds at **the same selected buffer** as B1.", "",
             "## Annual walk-forward folds", "",
             "| Variant | Train years | Test | Buffer | Train trades | Train mean R | Test trades | Test mean R | PF | Return | Max closed DD | P60 minutes | P20 width/ATR |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in ARMS:
        for row in folds.loc[folds.arm == arm].itertuples(index=False):
            train = training.loc[(training.arm == arm) & (training.fold == row.fold) &
                                 (np.isclose(training.buffer, row.selected_buffer))].iloc[0]
            lines.append(f"| {'B1' if arm == ARMS[0] else 'B2'} | {row.train_start}–{row.train_end_inclusive} | {row.test_year} | {row.selected_buffer:.2f} | {int(train.trades)} | {_fmt(row.selected_train_mean_r,4)} | {row.trades} | {_fmt(row.mean_r,4)} | {row.profit_factor:.3f} | {_fmt(row.return_pct,2)}% | {row.max_closed_dd_pct:.2f}% | {_fmt(row.cutoff_minutes,1)} | {_fmt(row.min_width_atr,3)} |")
    lines += ["", "## Combined unseen 2021–2024", "",
              "| Variant | Trades | Mean R | Median R | PF | Return | Max closed DD | Long mean R | Short mean R | Positive years | Longest losing streak | Max drawdown duration |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    details = {}
    yearly_lines = []
    for arm in ARMS:
        row = summaries.loc[arm]
        selected_folds = folds.loc[folds.arm == arm]
        extra = _extra(trades[arm], selected_folds)
        details[arm] = extra
        lines.append(f"| {'B1' if arm == ARMS[0] else 'B2'} | {int(row.trades)} | {_fmt(row.mean_r,4)} | {_fmt(extra['median_r'],4)} | {row.profit_factor:.3f} | {_fmt(row.return_pct,2)}% | {row.max_closed_dd_pct:.2f}% | {_fmt(extra['long_expectancy_r'],4)} | {_fmt(extra['short_expectancy_r'],4)} | {extra['profitable_years']}/4 | {extra['longest_losing_streak']} | {extra['max_closed_drawdown_duration_days']:.0f} calendar days |")
        yearly_lines.append(f"{'B1' if arm == ARMS[0] else 'B2'} yearly returns: " +
                            ", ".join(f"{year} {_fmt(value,2)}%" for year, value in extra["yearly_returns_pct"].items()) + ".")
    lines += ["", *yearly_lines]
    pair = paired.loc[ARMS[1]]
    lines += ["", "## B2 versus B1: fixed-buffer attribution", "",
              f"B2 kept **{len(kept)}** baseline sessions (baseline mean R {_fmt(kept.R_result.mean(),4)}) and removed **{len(removed)}** (mean R {_fmt(removed.R_result.mean(),4)}). The B2–B1 mean-R difference is {_fmt(pair.expectancy_difference,4)}. A {spec['bootstrap']['draws']:,}-draw session-date block bootstrap gives 95% CI [{_fmt(pair.ci_low,4)}, {_fmt(pair.ci_high,4)}] and P(Δ ≤ 0) = {pair.p_le_zero:.3f}. The interval describes this development-period sample, not independent confirmation of an edge.",
              "", "## Interpretation", "",
              "The prespecified OR-length sweep was not run: the 60-minute B1 and B2 results do not meet the clearly-negative trigger (negative mean R and return, with PF below 1). No further parameters were searched.", "",
              f"Strategy A legacy reproduction: **{a_check}**. Strategy A's old result is {a['trades']} trades, {_fmt(a['mean_r'],4)} mean R, PF {a['profit_factor']:.3f}, {_fmt(a['return_pct'],2)}% return and {a['max_closed_dd_pct']:.2f}% closed DD. Its frozen-filter arm had {int(a_arms.loc['both_1600_fixed'].trades)} trades and {_fmt(a_arms.loc['both_1600_fixed'].mean_r,4)} mean R. Those are different clock windows and cannot be interpreted as tests of the true 08:20 NY session.",
              "", "All results are modeled M1-bar outcomes; the stop-first convention resolves intraminute ambiguity conservatively. The buffer family and filters were shaped during prior development-period work, so the annual walk-forward is not a fully untouched experiment. No 2025+ strategy outcome was read.\n"]
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    comparison = ["# Broker-clock Strategy A and true-New-York Strategy B", "",
                  "| | Strategy A | Strategy B |", "| --- | --- | --- |",
                  "| Clock | Stored FTMO wall time treated as UTC | FTMO wall time decoded as New York + 7 h |",
                  "| Range on New York clock | 06:20–07:20 EST; 05:20–06:20 EDT | 08:20–09:20 EST/EDT |",
                  "| Exit on New York clock | 14:00 EST; 13:00 EDT | 16:00 EST/EDT |",
                  f"| Baseline trades / mean R | {a['trades']} / {_fmt(a['mean_r'],4)} | {int(summaries.loc[ARMS[0]].trades)} / {_fmt(summaries.loc[ARMS[0]].mean_r,4)} |",
                  f"| Baseline PF / return / max closed DD | {a['profit_factor']:.3f} / {_fmt(a['return_pct'],2)}% / {a['max_closed_dd_pct']:.2f}% | {summaries.loc[ARMS[0]].profit_factor:.3f} / {_fmt(summaries.loc[ARMS[0]].return_pct,2)}% / {summaries.loc[ARMS[0]].max_closed_dd_pct:.2f}% |",
                  f"| Frozen-filter trades / mean R | {int(a_arms.loc['both_1600_fixed'].trades)} / {_fmt(a_arms.loc['both_1600_fixed'].mean_r,4)} | {int(summaries.loc[ARMS[1]].trades)} / {_fmt(summaries.loc[ARMS[1]].mean_r,4)} |",
                  "", "These are distinct session hypotheses on the same 2018–2024 development cache. The corrected-clock result does not revise prior Strategy A output. Neither strategy is established as superior or live-ready by these selected development-period results. The 2025+ final holdout remains sealed.", ""]
    (output_dir.parent / "session_comparison.md").write_text("\n".join(comparison), encoding="utf-8")
    summary = {arm: {"trades": int(summaries.loc[arm].trades),
                      "mean_r": float(summaries.loc[arm].mean_r),
                      "profit_factor": float(summaries.loc[arm].profit_factor),
                      "return_pct": float(summaries.loc[arm].return_pct),
                      "max_closed_dd_pct": float(summaries.loc[arm].max_closed_dd_pct), **details[arm]}
               for arm in ARMS}
    (output_dir / "summary.json").write_text(json.dumps({"clock": "PASS", "strategy_a_reproduction": a_check,
                                                           "variants": summary, "holdout_touched": False}, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/strategy_b"))
    parser.add_argument("--a-reproduction", type=Path)
    args = parser.parse_args()
    summary = render(args.results_dir, args.output_dir, a_reproduction=args.a_reproduction)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
