"""Run the ORB scalping EA on cached FTMO ticks and write a standard run folder.

The folder (outputs/runs/<stamp>/ by default) holds parameters.json, trades.csv,
metrics.json, diagnostics.json and tester_report.json, so the dashboard's history,
trade list and strategy tester report read it like any other backtest.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import holdout_note
from .report import metrics
from .tester_report import tester_report
from .tick_scalper import TickScalper, ledger, load_scalper_config, serialise

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "tick_scalper.json"


def _json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, help="fresh run folder (default: a new outputs/runs stamp)")
    args = parser.parse_args(argv)
    config = load_scalper_config(args.config)
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:6]}"
    output = args.output_dir or ROOT / config.output_dir / stamp
    if not output.is_absolute():
        output = ROOT / output
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"run directory already contains files: {output}")
    output.mkdir(parents=True, exist_ok=True)
    _json(output / "parameters.json", serialise(config))
    print(f"Tick scalper: {config.symbol} {config.start} to {config.development_end} (exclusive), "
          f"range {config.range_open} NY + {config.range_minutes}m, cutoff {config.cutoff} NY", flush=True)
    started = time.monotonic()
    trades, diagnostics = TickScalper(config).run()
    print(f"Simulated {diagnostics['days']:,} weekdays in {time.monotonic() - started:.0f}s; "
          f"{diagnostics['trades']:,} trades", flush=True)
    _json(output / "diagnostics.json", diagnostics)
    if trades.empty:
        print("No trades; nothing more to report.")
        return 0
    trades = ledger(trades)
    trades.to_csv(output / "trades.csv", index=False)
    summary, monthly, yearly = metrics(trades, config)
    _json(output / "metrics.json", summary)
    monthly.to_csv(output / "monthly_returns.csv", index=False)
    yearly.to_csv(output / "yearly_returns.csv", index=False)
    report = tester_report(trades, config.initial_equity, config.session_timezone)
    _json(output / "tester_report.json", report["stats"])
    s = report["stats"]
    reasons = trades.exit_reason.value_counts().to_dict()
    print(f"Run folder: {output}")
    print(f"Trades {s['total_trades']:,} (long {s['long_trades']}, short {s['short_trades']}) | "
          f"win {s['profit_trades_pct']:.1f}% | avg win {s['average_profit_trade']:.2f} vs "
          f"avg loss {s['average_loss_trade']:.2f} | PF {s['profit_factor'] or float('nan'):.2f}")
    print(f"Net {s['total_net_profit']:+,.2f} -> balance {s['final_balance']:,.2f} | "
          f"max balance DD {s['balance_drawdown']['relative_pct']:.2f}% | exits {reasons}")
    print(f"Skipped/blocked: { {k: v for k, v in diagnostics.items() if k not in ('days', 'trades') and v} }")
    print(holdout_note(config), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
