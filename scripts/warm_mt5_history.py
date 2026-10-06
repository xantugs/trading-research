#!/usr/bin/env python3
"""Force the MT5 terminal to build deep M1 history for a symbol, newest year first.

A cold symbol has no local history, and asking MetaTrader straight for the oldest
range makes it fetch everything at once, which looks like a hang. This walks
backwards a year at a time so each request is small, prints before every request
so a stall is visible, and reports how far back the server actually goes.

    .venv\\Scripts\\python scripts\\warm_mt5_history.py --symbol XAGUSD

If a year returns 0 bars, open an M1 chart for the symbol in the terminal, press
Home to jump to the oldest bar, and check Tools > Options > Charts >
"Max bars in chart" is set high (Unlimited).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAGUSD")
    parser.add_argument("--from-year", type=int, default=2018)
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
        if not mt5.symbol_select(args.symbol, True):
            print(f"Symbol {args.symbol!r} could not be selected: {mt5.last_error()}", file=sys.stderr)
            return 1
        print(f"Warming {args.symbol} M1 history. Each line prints before its request.", flush=True)
        print("  latest bars ...", end=" ", flush=True)
        recent = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M1, 0, 5000)
        if recent is None or not len(recent):
            print(f"none ({mt5.last_error()})", flush=True)
            print("The terminal returned no recent bars; open an M1 chart for this symbol first.",
                  file=sys.stderr)
            return 1
        newest = datetime.fromtimestamp(float(recent[-1]["time"]), timezone.utc)
        print(f"{len(recent):,} bars, newest {newest:%Y-%m-%d %H:%M}", flush=True)

        reached = None
        this_year = datetime.now(timezone.utc).year
        for year in range(this_year, args.from_year - 1, -1):
            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = start + timedelta(days=8)
            print(f"  {year} ...", end=" ", flush=True)
            rates = mt5.copy_rates_range(args.symbol, mt5.TIMEFRAME_M1, start, end)
            count = 0 if rates is None else len(rates)
            print(f"{count:,} bars in the first week", flush=True)
            if count:
                reached = year
        print()
        if reached is None or reached > args.from_year:
            print(f"Deepest year reached: {reached or 'none'}. "
                  f"{args.symbol} history does not clearly reach {args.from_year}.", file=sys.stderr)
            print("Raise 'Max bars in chart', press Home on an M1 chart, then re-run. "
                  "If it still stops short, the broker does not retain that history.", file=sys.stderr)
            return 2
        print(f"{args.symbol} M1 history reaches {args.from_year}. The cache download can run now.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
