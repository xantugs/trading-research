#!/usr/bin/env python3
"""Reproducible first-pass XAUUSD ORB research runner."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace

from orb_research.backtester import backtest
from orb_research.config import load_config
from orb_research.data_loader import load_m1
from orb_research.report import write_baseline
from orb_research.plots import write_baseline_plots
from orb_research.report import metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/xauusd_orb_research.json")
    parser.add_argument("--terminal-path", help="Path to the intended broker's terminal64.exe")
    parser.add_argument("--refresh-data", action="store_true", help="Re-fetch cached M1 months")
    parser.add_argument("--no-plots", action="store_true", help="Skip HTML plot generation")
    parser.add_argument("--post-baseline-analysis", action="store_true",
                        help="After validating the baseline, run Monte Carlo and prop-rule scenarios")
    parser.add_argument("--prop-rules", default="configs/prop_research_example.json",
                        help="Illustrative prop-rule JSON; use only after verifying actual program rules")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        m1, provenance = load_m1(config, refresh=args.refresh_data, terminal_path=args.terminal_path)
        print(f"Loaded {len(m1):,} M1 bars from {m1.index[0]} to {m1.index[-1]}")
        trades, diagnostics = backtest(m1, config)
        print(f"Signals {diagnostics['signals']:,}; trades {len(trades):,}; skipped {diagnostics['skipped']}")
        report = write_baseline(trades, diagnostics, provenance, config)
        if not args.no_plots:
            _, monthly, yearly = metrics(trades, config)
            write_baseline_plots(trades, monthly, yearly, config.output_dir, config.initial_equity)
        if args.post_baseline_analysis:
            from pathlib import Path
            from orb_research.monte_carlo import trade_sequence_monte_carlo
            from orb_research.prop_simulator import load_prop_rules, simulate_prop, summarize_prop
            from orb_research.statistics import confidence_intervals

            out = Path(config.output_dir)
            mc_summary, mc_paths = trade_sequence_monte_carlo(
                trades, config.risk_percent, seed=config.random_seed)
            mc_summary.to_csv(out / "monte_carlo_summary.csv", index=False)
            mc_paths.to_csv(out / "monte_carlo_paths.csv", index=False)
            confidence_intervals(trades, seed=config.random_seed).to_csv(
                out / "confidence_intervals.csv", index=False)
            prop = simulate_prop(trades, load_prop_rules(args.prop_rules))
            prop.to_csv(out / "prop_simulation_results.csv", index=False)
            summarize_prop(prop).to_csv(out / "prop_simulation_summary.csv", index=False)
            cost_rows = []
            for factor in (1.0, 1.5, 2.0):
                cost_config = replace(
                    config, spread_multiplier=config.spread_multiplier * factor,
                    slippage_price=config.slippage_price * factor,
                    commission_per_lot_side=config.commission_per_lot_side * factor,
                )
                cost_trades, _ = backtest(m1, cost_config)
                if cost_trades.empty:
                    cost_rows.append({"cost_factor": factor, "trades": 0,
                                      "expectancy_r": None, "net_return_pct": None,
                                      "max_drawdown_pct": None})
                else:
                    cost_summary, _, _ = metrics(cost_trades, cost_config)
                    cost_rows.append({
                        "cost_factor": factor, "trades": cost_summary["trades"],
                        "expectancy_r": cost_summary["expectancy_r"],
                        "net_return_pct": cost_summary["net_return_pct"],
                        "max_drawdown_pct": cost_summary["max_drawdown_pct"],
                    })
            import pandas as pd
            pd.DataFrame(cost_rows).to_csv(out / "cost_sensitivity.csv", index=False)
        print(report.resolve())
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Research run stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
