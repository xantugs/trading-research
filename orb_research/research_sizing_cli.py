"""Research-sizing sensitivity for both distinct broker-clock hypotheses.

This is a sampling correction, not a suggested account size or live risk plan.
The original $5,000 Strategy A and B outputs are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from .backtester import minimum_lot_feasibility
from .config import load_config
from .data_loader import decode_broker_clock, load_m1, validate_m1
from .filter_arms import Arm, run_arms, write_results
from .strategy import find_signals
from .walk_forward import filter_signals, run_walk_forward, validate_frozen_config
from scripts.verify_broker_clock import verify
from scripts.strategy_b_report import _extra, _fmt


ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "configs" / "published_baselines.json"
RESEARCH_EQUITY = 100_000.0


def _skip(row) -> int:
    value = row.test_skipped
    if isinstance(value, str):
        value = json.loads(value)
    return int(value.get("below_min_lot", 0))


def _write_a_sensitivity(path: Path, result: dict, config, provenance: dict) -> None:
    path.mkdir(parents=True, exist_ok=False)
    training, folds = result["training"].copy(), result["folds"].copy()
    training["skipped"] = training.skipped.map(json.dumps)
    folds["test_skipped"] = folds.test_skipped.map(json.dumps)
    training.to_csv(path / "training_selection.csv", index=False)
    folds.to_csv(path / "folds.csv", index=False)
    result["oos_trades"].to_csv(path / "oos_trades.csv", index=False)
    (path / "summary.json").write_text(json.dumps(result["combined"], indent=2) + "\n", encoding="utf-8")
    spec = {"purpose": "Strategy A legacy-clock research-sizing sensitivity, not a replacement result",
            "initial_equity": RESEARCH_EQUITY, "sizing": "0.5% risk; unchanged 0.01 min/step and costs",
            "broker_clock": "stored_as_utc", "holdout_used": False,
            "base_config": asdict(config), "data_provenance": provenance}
    (path / "spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    combined = result["combined"]
    lines = ["# Strategy A — research-sizing sensitivity only", "",
             "The original Strategy A broker-clock results, configuration and output folders are unchanged. "
             "This separate $100,000 simulated-equity run removes the $5,000 minimum-lot sampling constraint. "
             "It is not a live account-size recommendation or a re-derivation of the published Strategy A result.", "",
             f"Trades {combined['trades']}; mean R {_fmt(combined['mean_r'],4)}; "
             f"PF {combined['profit_factor']:.3f}; return {_fmt(combined['return_pct'],2)}%; "
             f"max closed DD {combined['max_closed_dd_pct']:.2f}%.", "",
             "| Test year | Buffer | Trades | Mean R | Return | Below-min-lot skips | $5k-infeasible signals / valid |",
             "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in folds.itertuples(index=False):
        lines.append(f"| {row.test_year} | {row.selected_buffer:.2f} | {row.trades} | "
                     f"{_fmt(row.mean_r,4)} | {_fmt(row.return_pct,2)}% | {_skip(row)} | "
                     f"{int(row.test_5k_infeasible_signals)}/"
                     f"{int(row.test_5k_valid_sizing_signals)} |")
    lines += ["", "Only 2018–2024 development bars were read. The 2025+ holdout remains sealed.", ""]
    (path / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _write_b_report(path: Path, results: dict, a_result: dict) -> dict:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    old = published["strategy_b"]
    old_a = published["strategy_a"]
    arms = results["arms"]
    keys = ("base_1600_fixed", "both_1600_fixed")
    pair = results["paired"].set_index("arm").loc[keys[1]]
    kept = arms[keys[0]]["oos"]
    filtered = arms[keys[1]]["oos"]
    removed = kept.loc[~kept.session_date.isin(filtered.session_date)]
    if len(kept) - len(removed) != len(filtered):
        raise ValueError("B2 must retain only B1 sessions at fixed buffers")
    lines = ["# Strategy B — research-sizing correction", "",
             "**Prespecified interpretation:** research sizing removes minimum-lot censoring without changing "
             "the broker's 0.01-lot minimum/step, 0.5% risk fraction, entry rule, stop, target, costs or clock. "
             "The historical $5,000 runs are preserved for comparison. The $100,000 simulated balance is a "
             "sampling convention, not a proposed trading account.", "",
             "The P60 timing and P20 range-width filters are retired for Strategy B; B2 below is a "
             "one-time transfer check, not a proposed rule. The 4×4 sweep uses no filters.", "",
             "| Strategy B variant | Sizing | Trades | Mean R | PF | Return | Max closed DD | Long mean R | Short mean R | Positive years |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    summary = {}
    for key, label in zip(keys, ("B1", "B2")):
        item = arms[key]
        current = item["combined"]
        extra = _extra(item["oos"], item["folds"], RESEARCH_EQUITY)
        summary[key] = {**current, **extra}
        previous = old[key]
        for sizing, row in (("$5k original", previous), ("$100k research", {**current, **extra})):
            lines.append(f"| {label} | {sizing} | {row['trades']} | {_fmt(row['mean_r'],4)} | "
                         f"{row['profit_factor']:.3f} | {_fmt(row['return_pct'],2)}% | "
                         f"{row['max_closed_dd_pct']:.2f}% | "
                         f"{_fmt(row.get('long_expectancy_r'),4)} | "
                         f"{_fmt(row.get('short_expectancy_r'),4)} | "
                         f"{row.get('profitable_years','—')}/4 |")
    lines += ["", "## Fold sizing and feasibility", "",
              "The $5,000 infeasible share is a counterfactual statistic among signals with a valid next-M1 entry and stop; "
              "it never filters the $100,000 sample. Other execution skips remain reported separately.", "",
              "The original $5,000 skip counts follow its own fold-selected buffers; the corrected "
              "research choices differ in 2023. The counterfactual share uses exactly $5,000 each day, "
              "while the original run carried its equity forward.", "",
              "| Arm | Test year | Buffer | Research trades | Original $5k below-min skips | Research below-min skips | $5k-infeasible / valid signals | Share |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for key, label in zip(keys, ("B1", "B2")):
        for row in arms[key]["folds"].itertuples(index=False):
            share = row.test_5k_infeasible_share
            old_skips = old[key]["below_min_lot_skips"][int(row.test_year) - 2021]
            current = _skip(row)
            fraction = (f"{int(row.test_5k_infeasible_signals)}/"
                        f"{int(row.test_5k_valid_sizing_signals)}")
            lines.append(f"| {label} | {row.test_year} | {row.selected_buffer:.2f} | {row.trades} | "
                         f"{old_skips} | {current} | {fraction} | "
                         f"{share:.1%} |" if pd.notna(share) else
                         f"| {label} | {row.test_year} | {row.selected_buffer:.2f} | {row.trades} | "
                         f"{old_skips} | {current} | {fraction} | — |")
    lines += ["", "## Fixed-buffer B2 attribution", "",
              f"B2 kept {len(filtered)} B1 sessions; their mean R was {_fmt(filtered.R_result.mean(),4)}. "
              f"It removed {len(removed)} sessions with mean R {_fmt(removed.R_result.mean(),4)}. "
              f"B2 minus B1 mean R: {_fmt(pair.expectancy_difference,4)}, session-block "
              f"95% CI [{_fmt(pair.ci_low,4)}, {_fmt(pair.ci_high,4)}], "
              f"P(Δ ≤ 0) {pair.p_le_zero:.3f} (4,000 draws).", "",
              "## Strategy A sensitivity", "",
              f"Published $5k Strategy A: {old_a['trades']} trades, {_fmt(old_a['mean_r'],4)} mean R, "
              f"PF {old_a['profit_factor']:.3f}, {_fmt(old_a['return_pct'],2)}% return; "
              f"published filter arm {old_a['frozen_filter_trades']} trades, "
              f"{_fmt(old_a['frozen_filter_mean_r'],4)} mean R. "
              f"Separate $100k Strategy A sensitivity: {a_result['combined']['trades']} trades, "
              f"{_fmt(a_result['combined']['mean_r'],4)} mean R, "
              f"PF {a_result['combined']['profit_factor']:.3f}, "
              f"{_fmt(a_result['combined']['return_pct'],2)}% return. "
              "This does not replace the published Strategy A result.", "",
              "No 2025+ strategy outcome was accessed.", ""]
    (path / "research_sizing_report.md").write_text("\n".join(lines), encoding="utf-8")
    (path / "research_sizing_summary.json").write_text(
        json.dumps({"variants": summary, "holdout_used": False}, indent=2, default=str) + "\n",
        encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/strategy_b_0820.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--a-output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    base = load_config(args.config)
    validate_frozen_config(base)
    if base.broker_clock != "new_york_plus_7" or base.initial_equity != 5000:
        raise ValueError("research sizing starts from the frozen $5k Strategy B config")
    raw, provenance = load_m1(replace(base, broker_clock="stored_as_utc"))
    clock = verify(raw, base)
    if not clock["pass"]:
        raise ValueError("FTMO clock verification failed; no strategy outcome computed")
    research_b = replace(base, initial_equity=RESEARCH_EQUITY)
    corrected = validate_m1(decode_broker_clock(raw, research_b), research_b)
    arms = (Arm("base_1600_fixed"), Arm("both_1600_fixed", early=True, width=True))
    print("Running Strategy B research-sizing B1/B2 on 2018–2024 only...", flush=True)
    results = run_arms(corrected, research_b, arms, emit=lambda message: print(message, flush=True))
    path = args.output_dir
    write_results(path, results, research_b, provenance)
    (path / "clock_verification.json").write_text(json.dumps(clock, indent=2) + "\n", encoding="utf-8")
    print("Running one separate Strategy A research-sizing sensitivity...", flush=True)
    research_a = replace(base, broker_clock="stored_as_utc", initial_equity=RESEARCH_EQUITY,
                         session_name="legacy_broker_clock_window")
    a_result = run_walk_forward(raw, research_a, emit=lambda message: print(message, flush=True))
    for idx, row in a_result["folds"].iterrows():
        prefix = raw.loc[raw.index < pd.Timestamp(f"{int(row.test_year) + 1}-01-01", tz="UTC")]
        candidate = replace(research_a, breakout_buffer_atr=float(row.selected_buffer))
        signals, _ = find_signals(prefix, candidate)
        selected = filter_signals(signals, int(row.test_year), int(row.test_year) + 1)
        feasibility = minimum_lot_feasibility(prefix, selected, candidate)
        a_result["folds"].loc[idx, "test_5k_valid_sizing_signals"] = feasibility["valid_sizing_signals"]
        a_result["folds"].loc[idx, "test_5k_infeasible_signals"] = feasibility["infeasible_at_5k"]
        a_result["folds"].loc[idx, "test_5k_infeasible_share"] = feasibility["infeasible_share"]
    _write_a_sensitivity(args.a_output_dir, a_result, research_a, provenance)
    summary = _write_b_report(path, results, a_result)
    skips = {key: [_skip(row) for row in item["folds"].itertuples(index=False)]
             for key, item in results["arms"].items()}
    (path / "sizing_spec.json").write_text(json.dumps({
        "purpose": "correct minimum-lot sampling censoring without changing execution rules",
        "research_initial_equity": RESEARCH_EQUITY, "reference_equity": 5000,
        "risk_percent": 0.5, "lot_step": base.lot_step, "min_lot": base.min_lot,
        "candidate_buffers": [0.25, 0.35, 0.45, 0.55],
        "clock_verified": True, "holdout_used": False,
        "code_sha256": {name: hashlib.sha256((ROOT / "orb_research" / name).read_bytes()).hexdigest()
                        for name in ("backtester.py", "filter_arms.py", "walk_forward.py")},
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"clock": "PASS", "strategy_a_published_reproduction": "PASS (prior run)",
                      "research_sizing_skips": skips, "B": summary,
                      "a_sensitivity": a_result["combined"], "holdout_touched": False},
                     indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
