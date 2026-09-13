#!/usr/bin/env python3
"""Probe native bar and real-tick coverage on the MT5 server currently logged in."""
from __future__ import annotations

import argparse
import calendar
import sys
from datetime import date, datetime, timedelta, timezone


def _sample_days(year: int, month: int):
    """Yield likely trading days near mid-month, then the rest of the month."""
    days = calendar.monthrange(year, month)[1]
    ordered = sorted(range(1, days + 1), key=lambda day: (abs(day - 15), day))
    for day in ordered:
        candidate = date(year, month, day)
        if candidate.weekday() < 5:
            yield candidate


def _ticks_on_day(mt5, symbol: str, day: date):
    # Noon UTC is inside the liquid part of the XAUUSD session throughout the year.
    start = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
    if ticks is None:
        raise RuntimeError(f"tick request failed for {day}: {mt5.last_error()}")
    return ticks


def _month_sample(mt5, symbol: str, year: int, month: int):
    for attempt, day in enumerate(_sample_days(year, month), start=1):
        ticks = _ticks_on_day(mt5, symbol, day)
        if len(ticks):
            return day, ticks
        # Three weekdays are enough to distinguish a holiday from an absent month.
        if attempt == 3:
            break
    return None, None


def _fmt_tick_time(tick) -> str:
    time_msc = getattr(tick, "time_msc", None)
    if time_msc is None:
        time_msc = tick["time_msc"]
    return datetime.fromtimestamp(float(time_msc) / 1000, timezone.utc).isoformat()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Find the approximate first native real-tick date exposed by the MT5 "
            "server currently logged in. The probe requests only five-minute samples."
        )
    )
    parser.add_argument("--symbol", default="XAUUSD", help="Exact broker symbol (default: XAUUSD)")
    parser.add_argument(
        "--start-year", type=int, default=max(2000, datetime.now(timezone.utc).year - 10),
        help="Oldest year to test (default: ten years ago)",
    )
    parser.add_argument("--terminal-path", help="Optional path to terminal64.exe")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError:
        print('MetaTrader5 is missing. Run: .venv\\Scripts\\python -m pip install -e ".[mt5]"',
              file=sys.stderr)
        return 1

    initialized = mt5.initialize(path=args.terminal_path) if args.terminal_path else mt5.initialize()
    if not initialized:
        print(f"MT5 initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 1

    try:
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if account is None or terminal is None:
            raise RuntimeError(f"MT5 account or terminal information is unavailable: {mt5.last_error()}")
        if not terminal.connected:
            raise RuntimeError("MT5 is open but is not connected to its trading server")
        if not mt5.symbol_select(args.symbol, True):
            raise RuntimeError(f"symbol {args.symbol!r} is unavailable: {mt5.last_error()}")
        info = mt5.symbol_info(args.symbol)
        if info is None:
            raise RuntimeError(f"symbol details are unavailable for {args.symbol!r}")

        current_year = datetime.now(timezone.utc).year
        if not 2000 <= args.start_year <= current_year:
            raise ValueError(f"--start-year must be between 2000 and {current_year}")

        print(f"Connected: {account.company} / {account.server}")
        print(f"Terminal:  {terminal.path}")
        print(
            f"Symbol:    {args.symbol} | digits {info.digits} | point {info.point:g} | "
            f"tick size {info.trade_tick_size:g} | tick value {info.trade_tick_value:g} | "
            f"contract {info.trade_contract_size:g}"
        )
        print("\nSampling native bid/ask ticks (timestamps below are UTC):", flush=True)

        first_year = None
        for year in range(args.start_year, current_year + 1):
            found = None
            for month in (1, 4, 7, 10):
                if year == current_year and month > datetime.now(timezone.utc).month:
                    continue
                sample_day, ticks = _month_sample(mt5, args.symbol, year, month)
                if sample_day is not None:
                    found = (sample_day, ticks)
                    break
            if found is None:
                print(f"  {year}: no sampled ticks", flush=True)
            else:
                first_year = first_year or year
                print(f"  {year}: yes ({found[0]}, {len(found[1]):,} ticks/5 min)", flush=True)

        if first_year is None:
            print("\nNo real ticks were returned in the requested years.")
            return 2

        first_month = None
        for month in range(1, 13):
            sample_day, _ = _month_sample(mt5, args.symbol, first_year, month)
            if sample_day is not None:
                first_month = month
                break
        if first_month is None:
            raise RuntimeError(f"coverage disappeared while refining {first_year}")

        first_day = None
        first_ticks = None
        for candidate in sorted(_sample_days(first_year, first_month)):
            ticks = _ticks_on_day(mt5, args.symbol, candidate)
            if len(ticks):
                first_day, first_ticks = candidate, ticks
                break
        if first_day is None:
            raise RuntimeError("coverage disappeared while refining the first month")

        bar_start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=timezone.utc)
        bars = mt5.copy_rates_range(
            args.symbol, mt5.TIMEFRAME_M1, bar_start, bar_start + timedelta(days=1)
        )
        if bars is None:
            raise RuntimeError(f"M1 request failed: {mt5.last_error()}")

        latest = mt5.symbol_info_tick(args.symbol)
        latest_text = _fmt_tick_time(latest) if latest is not None else "unavailable"
        print("\nCoverage result:")
        print(f"  First confirmed tick day: {first_day}")
        print(f"  First sampled tick:       {_fmt_tick_time(first_ticks[0])}")
        print(f"  M1 bars on that UTC day:  {len(bars):,}")
        print(f"  Current feed tick:        {latest_text}")
        print(
            "\nUse this broker symbol in Strategy Tester with "
            "'Every tick based on real ticks'. The reported first day is the first "
            "day confirmed by five-minute samples, not a contractual retention guarantee."
        )
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"Probe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
