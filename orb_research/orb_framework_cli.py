"""Compare ORB-framework range/entry timeframe pairs on one load of development M1 bars.

Reproduces the ORB-framework EA experiment: the opening range is read on one
timeframe and breakouts are confirmed on another, so a 15-minute range can be
entered on the first 5-minute (or 1-minute) close outside it instead of waiting
for a 15-minute close.  Every pair shares the preset's other rules; only the two
timeframes change.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .backtester import backtest
from .config import RANGE_MINUTES, SIGNAL_MINUTES, holdout_note, load_config
from .data_loader import load_m1
from .report import metrics


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "orb_framework.json"
# The video's two runs: the original rules (both 15m), then 5-minute entries.
DEFAULT_PAIRS = ((15, 15), (15, 5))


def parse_pair(text: str) -> tuple[int, int]:
    try:
        range_minutes, entry_minutes = (int(part) for part in text.split("/"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("pairs are RANGE/ENTRY minutes, e.g. 15/5") from exc
    if range_minutes not in RANGE_MINUTES or entry_minutes not in SIGNAL_MINUTES:
        raise argparse.ArgumentTypeError(
            f"range must be one of {RANGE_MINUTES} and entry one of {SIGNAL_MINUTES}")
    return range_minutes, entry_minutes


def summarise(trades: pd.DataFrame, diagnostics: dict, config, label: str = "") -> dict:
    row = {"label": label or f"{config.range_minutes}/{config.signal_minutes}",
           "range_minutes": config.range_minutes, "entry_minutes": config.signal_minutes,
           "signals": diagnostics["signals"], "skipped": sum(diagnostics["skipped"].values())}
    if trades.empty:
        return {**row, "trades": 0}
    summary, _, _ = metrics(trades, config)
    directions = summary["by_direction"]
    hold_hours = (pd.to_datetime(trades.exit_time) - pd.to_datetime(trades.timestamp)
                  ).dt.total_seconds() / 3600
    return {
        **row, "trades": summary["trades"], "win_rate": summary["win_rate"],
        "long_trades": directions.get("long", {}).get("trades", 0),
        "long_win_rate": directions.get("long", {}).get("win_rate"),
        "short_trades": directions.get("short", {}).get("trades", 0),
        "short_win_rate": directions.get("short", {}).get("win_rate"),
        "mean_r": summary["expectancy_r"], "profit_factor": summary["profit_factor"],
        "final_balance": config.initial_equity * (1 + summary["net_return_pct"] / 100),
        "net_return_pct": summary["net_return_pct"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "average_hold_hours": float(hold_hours.mean()), "max_hold_hours": float(hold_hours.max()),
    }


def comparison_markdown(rows: list[dict], config, multiple: bool = False) -> str:
    def pct(value):
        return "n/a" if value is None else f"{100 * value:.1f}%"

    def num(value, digits=2):
        return "n/a" if value is None else f"{value:,.{digits}f}"

    window = (f"{config.symbol} on {config.expected_server}, {config.start} to "
              f"{config.development_end} (exclusive). {holdout_note(config)}.")
    if multiple:
        title = "# ORB configuration comparison"
        rules = (f"{window} Each run is the named file in `configs/`; its full parameters are in "
                 "`parameters.json`.")
    else:
        title = "# ORB-framework timeframe comparison"
        rules = (
            f"{window} Range from {config.session_open} New York, "
            f"no new entries from {config.session_exit}, "
            + ("positions held until stop or target" if config.position_exit == "stop_or_target"
               else f"flat at {config.session_exit}")
            + f", trades per session: {config.trades_per_session}, direction: {config.trade_direction}. "
            f"TP {config.reward_risk}R, stop {config.stop_method.replace('_', ' ')} "
            f"({config.stop_width_multiple:g}), risk {config.risk_percent}% of equity, "
            f"start balance {config.initial_equity:,.0f}.")
    lines = [
        title, "", rules, "",
        "| Run | Range | Entry | Trades | Win | Long (win) | Short (win) | Mean R | PF "
        "| Final balance | Return | Max DD | Avg hold h |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        head = f"| {row['label']} | {row['range_minutes']}m | {row['entry_minutes']}m"
        if not row["trades"]:
            lines.append(f"{head} | 0 | | | | | | | | | |")
            continue
        lines.append(
            f"{head} | {row['trades']:,} | "
            f"{pct(row['win_rate'])} | {row['long_trades']} ({pct(row['long_win_rate'])}) | "
            f"{row['short_trades']} ({pct(row['short_win_rate'])}) | {row['mean_r']:+.3f} | "
            f"{num(row['profit_factor'])} | {num(row['final_balance'], 0)} | "
            f"{row['net_return_pct']:+.1f}% | {num(row['max_drawdown_pct'], 1)}% | "
            f"{num(row['average_hold_hours'], 1)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pairs", type=parse_pair, nargs="+", default=list(DEFAULT_PAIRS),
                        metavar="RANGE/ENTRY", help="timeframe pairs in minutes (default: 15/15 15/5)")
    parser.add_argument("--configs", type=Path, nargs="+", metavar="CONFIG",
                        help="compare whole configurations instead of timeframe pairs; all must "
                             "share the first one's data source and period")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.configs:
        runs = [(path.stem, load_config(path)) for path in args.configs]
        base = runs[0][1]
        data_keys = ("symbol", "expected_server", "source", "cache_dir", "data_path", "start",
                     "development_end", "holdout_start", "holdout_opened", "broker_clock")
        for name, config in runs[1:]:
            if any(getattr(config, key) != getattr(base, key) for key in data_keys):
                raise ValueError(f"{name} uses different data or dates from {runs[0][0]}")
    else:
        base = load_config(args.config)
        runs = [(f"{r}/{e}", replace(base, range_minutes=r, signal_minutes=e))
                for r, e in args.pairs]
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:6]}"
    output = args.output_dir or ROOT / base.output_dir / "comparisons" / stamp
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory already contains files: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "parameters.json").write_text(json.dumps(
        {name: asdict(config) for name, config in runs}, indent=2) + "\n", encoding="utf-8")

    m1, _ = load_m1(base)
    print(f"Loaded {len(m1):,} M1 bars from {m1.index[0]} to {m1.index[-1]}", flush=True)
    rows = []
    for name, config in runs:
        config.validate()
        trades, diagnostics = backtest(m1, config)
        trades.to_csv(output / f"trades_{name.replace('/', '_')}.csv", index=False)
        row = summarise(trades, diagnostics, config, name)
        rows.append(row)
        detail = (f", win {100 * row['win_rate']:.1f}%, final balance {row['final_balance']:,.0f}, "
                  f"max DD {row['max_drawdown_pct']:.1f}%" if row["trades"] else "")
        print(f"{name} (range {config.range_minutes}m / entry {config.signal_minutes}m): "
              f"{row['trades']:,} trades{detail}", flush=True)
    pd.DataFrame(rows).to_csv(output / "comparison.csv", index=False)
    report = output / "comparison.md"
    report.write_text(comparison_markdown(rows, base, multiple=bool(args.configs)), encoding="utf-8")
    print(f"Comparison: {report}", flush=True)
    print(holdout_note(base), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
