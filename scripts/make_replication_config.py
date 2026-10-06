#!/usr/bin/env python3
"""Write a frozen-plan replication config for another FTMO symbol, from live broker specs.

Windows + MetaTrader 5 only. Connects to the terminal that is already logged in,
reads the exact contract specification for the requested symbol, checks that M1
history reaches the 2018 development start, and writes a configuration that keeps
every Strategy A choice fixed except the instrument.

    .venv\\Scripts\\python scripts\\make_replication_config.py --symbol XAGUSD

The two price-unit cost parameters are written as placeholders and must be
rescaled from the instrument's own recorded spread before any backtest.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "configs" / "walk_forward_base.json"
COVERAGE_MONTHS = ((2018, 1), (2020, 1), (2022, 1), (2024, 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAGUSD", help="Exact broker symbol")
    parser.add_argument("--expected-server", default="FTMO-Demo")
    parser.add_argument("--cache-root", default="C:/data")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "configs" / "strategy_a_silver.json")
    parser.add_argument("--terminal-path")
    args = parser.parse_args(argv)

    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError:
        print('MetaTrader5 is missing. Run: .venv\\Scripts\\python -m pip install -e ".[research-v2]"',
              file=sys.stderr)
        return 1
    ok = mt5.initialize(path=args.terminal_path) if args.terminal_path else mt5.initialize()
    if not ok:
        print(f"MT5 initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        account = mt5.account_info()
        if account is None:
            print("MT5 terminal is not logged in", file=sys.stderr)
            return 1
        print(f"Connected: {account.company} / {account.server}")
        if account.server != args.expected_server:
            print(f"Expected {args.expected_server!r}; switch accounts before continuing.",
                  file=sys.stderr)
            return 1
        if not mt5.symbol_select(args.symbol, True):
            stem = args.symbol[:3]
            matches = [s.name for s in (mt5.symbols_get() or []) if stem in s.name.upper()]
            print(f"Symbol {args.symbol!r} unavailable. Candidates: {matches or 'none found'}",
                  file=sys.stderr)
            return 1
        info = mt5.symbol_info(args.symbol)
        if info is None:
            print(f"No symbol metadata: {mt5.last_error()}", file=sys.stderr)
            return 1
        print(f"\n{args.symbol} contract specification")
        for label, value in (("point", info.point), ("contract size", info.trade_contract_size),
                             ("tick size", info.trade_tick_size), ("tick value", info.trade_tick_value),
                             ("volume min/step/max", f"{info.volume_min}/{info.volume_step}/{info.volume_max}"),
                             ("profit currency", info.currency_profit),
                             ("chart mode", "bid" if info.chart_mode == mt5.SYMBOL_CHART_MODE_BID else "last")):
            print(f"  {label:22} {value}")
        if info.chart_mode != mt5.SYMBOL_CHART_MODE_BID:
            print("\nThis symbol charts Last, not Bid; the research engine requires Bid bars.",
                  file=sys.stderr)
            return 1
        if account.currency != "USD" or info.currency_profit != "USD":
            print("\nResearch sizing requires a USD account and USD symbol P/L.", file=sys.stderr)
            return 1

        print("\nM1 coverage probe (bars in the first week of each sampled month):")
        reachable = True
        for year, month in COVERAGE_MONTHS:
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            end = datetime(year, month, 8, tzinfo=timezone.utc)
            rates = mt5.copy_rates_range(args.symbol, mt5.TIMEFRAME_M1, start, end)
            count = 0 if rates is None else len(rates)
            print(f"  {year}-{month:02d}: {count:,} bars")
            if year == 2018 and count == 0:
                reachable = False
        if not reachable:
            print("\nNo 2018 M1 history: the frozen 2018-2024 fold structure cannot be replicated.\n"
                  "Raise 'Max bars in chart' in the terminal, or stop here and report this.",
                  file=sys.stderr)
            return 1

        template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        config = dict(template)
        config.update({
            "symbol": args.symbol,
            "expected_server": args.expected_server,
            "cache_dir": f"{args.cache_root}/ftmo_{args.symbol.lower()}_m1",
            "tick_cache_dir": f"{args.cache_root}/ftmo_{args.symbol.lower()}_ticks",
            "output_dir": f"outputs/replication_{args.symbol.lower()}",
            "point": float(info.point),
            "contract_size": float(info.trade_contract_size),
            "lot_step": float(info.volume_step),
            "min_lot": float(info.volume_min),
            "max_lot": float(min(100.0, info.volume_max)),
            # Research sizing, as corrected on 18 September 2026.
            "initial_equity": 100000.0,
            # PLACEHOLDERS: rescale from this instrument's own recorded spread before backtesting.
            "slippage_price": float(template["slippage_price"]),
            "fallback_spread_price": float(template["fallback_spread_price"]),
        })
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.output}")
        print("slippage_price and fallback_spread_price are still gold's values; they must be "
              "rescaled from this symbol's recorded spread before any backtest.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
