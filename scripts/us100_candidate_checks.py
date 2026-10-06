#!/usr/bin/env python3
"""Run the checks pre-declared in docs/US100_CANDIDATE_PLAN.md and write the results.

C1 calendar years, C2 longs/shorts, C3 the 27-cell neighbourhood, C4 cost stress and
C5 the expanding walk-forward, then FTMO 2-step sizing by historical replay and a
day-block Monte Carlo.  Bars are applied exactly as written in the plan.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.backtester import backtest
from orb_research.config import load_config
from orb_research.data_loader import load_m1
from orb_research.prop_simulator import PropRules, simulate_prop, summarize_prop

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs/indices/ftmo_us100_orb_framework_15_15.json"
RISKS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
RULES = PropRules(account_size=10000.0, phase1_target_pct=10, phase2_target_pct=5,
                  daily_loss_limit_pct=5, max_loss_limit_pct=10, reset_timezone="Europe/Prague")


def r_stats(trades: pd.DataFrame) -> dict:
    r = trades.R_result
    return {"trades": len(trades), "mean_r": float(r.mean()) if len(r) else None,
            "net_r": float(r.sum()), "win_rate": float((trades.PnL > 0).mean()) if len(r) else None}


def check_years(trades: pd.DataFrame) -> dict:
    years = trades.timestamp.dt.tz_convert("America/New_York").dt.year
    rows = {int(y): r_stats(g) for y, g in trades.groupby(years)}
    counted = {y: v for y, v in rows.items() if 2022 <= y <= 2026}
    positive = sum(v["mean_r"] > 0 for v in counted.values())
    total = sum(v["net_r"] for v in counted.values())
    top_share = max(v["net_r"] for v in counted.values()) / total if total > 0 else None
    return {"years": rows, "positive_years": positive, "top_year_share_of_net_r": top_share,
            "pass": positive >= 3 and top_share is not None and top_share <= 0.60}


def run_cell(m1, base, range_minutes, reward, cutoff):
    config = replace(base, range_minutes=range_minutes, reward_risk=reward, session_exit=cutoff)
    config.validate()
    return backtest(m1, config)[0]


def walk_forward(cells: dict, candidate_key) -> dict:
    folds, pooled = [], []
    for year in (2023, 2024, 2025, 2026):
        cut = pd.Timestamp(f"{year}-01-01", tz="America/New_York")
        end = pd.Timestamp(f"{year + 1}-01-01", tz="America/New_York")
        scores = {}
        for key, trades in cells.items():
            train = trades[trades.timestamp < cut]
            scores[key] = float(train.R_result.mean()) if len(train) else -np.inf
        best_score = max(scores.values())
        chosen = candidate_key if scores[candidate_key] == best_score else max(scores, key=scores.get)
        test = cells[chosen][(cells[chosen].timestamp >= cut) & (cells[chosen].timestamp < end)]
        pooled.append(test.R_result)
        folds.append({"test_year": year, "chosen": list(chosen), "train_mean_r": scores[chosen], **r_stats(test)})
    pooled_r = pd.concat(pooled)
    positive = sum(f["mean_r"] is not None and f["mean_r"] > 0 for f in folds)
    return {"folds": folds, "pooled_mean_r": float(pooled_r.mean()), "positive_test_years": positive,
            "pass": float(pooled_r.mean()) > 0 and positive >= 3}


def monte_carlo(trades: pd.DataFrame, risk_pct: float, paths: int = 10000, seed: int = 20261007,
                min_days: int = 4, max_days: int = 2500) -> dict:
    """Redraw whole trading days (Prague-midnight days) with replacement through both phases."""
    days = trades.exit_time.dt.tz_convert(RULES.reset_timezone).dt.date
    blocks = [g.R_result.to_numpy(dtype=float) for _, g in trades.groupby(days, sort=True)]
    span = (trades.exit_time.max() - trades.timestamp.min()).days
    calendar_per_day = span / len(blocks)
    rng = np.random.default_rng(seed)
    risk = risk_pct / 100
    outcome = {"passed": 0, "daily_dd": 0, "total_dd": 0, "censored": 0}
    days_to_pass, trades_to_pass = [], []
    for _ in range(paths):
        used_days = used_trades = 0
        result = "censored"
        for target in (RULES.phase1_target_pct, RULES.phase2_target_pct):
            equity = start = RULES.account_size
            phase_days, phase_result = 0, "censored"
            while used_days < max_days:
                block = blocks[rng.integers(len(blocks))]
                day_open = equity
                breached = None
                for r in block:
                    equity += equity * risk * r
                    used_trades += 1
                    if equity <= day_open * (1 - RULES.daily_loss_limit_pct / 100):
                        breached = "daily_dd"
                        break
                    if equity <= start * (1 - RULES.max_loss_limit_pct / 100):
                        breached = "total_dd"
                        break
                used_days += 1
                phase_days += 1
                if breached:
                    phase_result = breached
                    break
                if equity >= start * (1 + target / 100) and phase_days >= min_days:
                    phase_result = "passed"
                    break
            result = phase_result
            if phase_result != "passed":
                break
        outcome[result] += 1
        if result == "passed":
            days_to_pass.append(used_days * calendar_per_day)
            trades_to_pass.append(used_trades)
    return {"risk_percent": risk_pct, "paths": paths,
            "p_pass_both": outcome["passed"] / paths,
            "p_breach_before_pass": (outcome["daily_dd"] + outcome["total_dd"]) / paths,
            "p_daily_breach": outcome["daily_dd"] / paths, "p_total_breach": outcome["total_dd"] / paths,
            "p_unresolved": outcome["censored"] / paths,
            "median_calendar_days_to_pass": float(np.median(days_to_pass)) if days_to_pass else None,
            "median_trades_to_pass": float(np.median(trades_to_pass)) if trades_to_pass else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/indices/us100_candidate" / stamp)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    base = load_config(CANDIDATE)
    m1, _ = load_m1(base)
    print(f"Loaded {len(m1):,} M1 bars", flush=True)
    candidate, _ = backtest(m1, base)
    candidate.to_csv(args.output_dir / "candidate_trades.csv", index=False)
    results = {"candidate": r_stats(candidate)}

    results["C1_years"] = check_years(candidate)
    results["C2_directions"] = {d: r_stats(g) for d, g in candidate.groupby("direction")}

    grid = list(product((5, 15, 30), (1.25, 1.5, 2.0), ("11:00", "12:00", "13:00")))
    cells = {}
    for i, key in enumerate(grid, 1):
        cells[key] = run_cell(m1, base, *key)
        print(f"  cell {i}/27 {key}: mean R {cells[key].R_result.mean():+.4f}", flush=True)
    candidate_key = (base.range_minutes, base.reward_risk, base.session_exit)
    means = {k: float(v.R_result.mean()) for k, v in cells.items()}
    positive_cells = sum(m > 0 for m in means.values())
    median_cell = float(np.median(list(means.values())))
    results["C3_neighbourhood"] = {
        "cells": [{"range_minutes": k[0], "reward_risk": k[1], "cutoff": k[2], **r_stats(v)} for k, v in cells.items()],
        "positive_cells": positive_cells, "median_cell_mean_r": median_cell,
        "candidate_mean_r": means[candidate_key],
        "pass": positive_cells >= 19 and median_cell >= 0.5 * means[candidate_key]}

    stressed = replace(base, spread_multiplier=2.0, slippage_price=base.slippage_price * 2,
                       financing_pct_annual=10.0)
    stressed_trades, _ = backtest(m1, stressed)
    spread_only, _ = backtest(m1, replace(base, spread_multiplier=2.0, slippage_price=base.slippage_price * 2))
    financing_only, _ = backtest(m1, replace(base, financing_pct_annual=10.0))
    results["C4_costs"] = {"spread_slippage_x2": r_stats(spread_only), "financing_10pct": r_stats(financing_only),
                           "combined": r_stats(stressed_trades),
                           "pass": float(stressed_trades.R_result.mean()) > 0}

    results["C5_walk_forward"] = walk_forward(cells, candidate_key)
    results["passes_robustness"] = all(results[k]["pass"] for k in
                                       ("C1_years", "C3_neighbourhood", "C4_costs", "C5_walk_forward"))

    replay = simulate_prop(candidate, RULES, risk_levels=RISKS)
    replay_summary = summarize_prop(replay)
    replay_summary.to_csv(args.output_dir / "ftmo_replay_summary.csv", index=False)
    results["sizing_replay"] = replay_summary.replace({np.nan: None}).to_dict(orient="records")
    mc = [monte_carlo(candidate, risk) for risk in RISKS]
    for row in mc:
        print(f"  MC {row['risk_percent']}%: pass {row['p_pass_both']:.1%}, breach {row['p_breach_before_pass']:.1%}", flush=True)
    results["sizing_monte_carlo"] = mc
    eligible = [row["risk_percent"] for row in mc if row["p_breach_before_pass"] <= 0.25]
    results["forward_test_risk_percent"] = max(eligible) if eligible else None

    (args.output_dir / "results.json").write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Robustness pass: {results['passes_robustness']} | forward-test risk: {results['forward_test_risk_percent']}")
    print(args.output_dir / "results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
