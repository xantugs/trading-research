"""Pull price history out of a running MetaTrader 5 terminal into CSV files.

Windows only: the ``MetaTrader5`` package talks to the terminal over IPC, so
MT5 must be open and logged in to the account whose data you want.  Times in
the output are BROKER SERVER TIME, exactly as the EA sees them.

    pip install MetaTrader5 pandas

    # M1 + M15 bars for the last few years
    python fetch_mt5.py --symbol NAS100 --from 2021-01-01 --to 2026-09-01 --tf M1 M15

    # real ticks (large!) for a shorter window, plus M1 bars rebuilt from them
    python fetch_mt5.py --symbol NAS100 --from 2025-01-01 --to 2025-07-01 --ticks

Outputs land in ./data as  <SYMBOL>_<TF>.csv  with the columns
    time,open,high,low,close,tick_volume,spread,real_volume
and the script prints the symbol's contract details you need for the Python
``Params`` (point, point_value, lot step ...).

How far back the broker serves M1/tick history varies (often 1-3 years of
ticks, longer for bars).  Whatever the terminal has is what you get; the
script says how many bars it received per month so gaps are visible.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from orb.timezones import to_server_time

TF_NAMES = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15", "M30": "TIMEFRAME_M30",
            "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4", "D1": "TIMEFRAME_D1"}


def month_starts(start: datetime, end: datetime):
    cur = start.replace(day=1)
    while cur < end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt, end)
        cur = nxt


def fetch_bars(mt5, symbol: str, tf: str, start: datetime, end: datetime) -> pd.DataFrame:
    frames = []
    tf_const = getattr(mt5, TF_NAMES[tf])
    for a, b in month_starts(start, end):
        rates = mt5.copy_rates_range(symbol, tf_const, a, b)
        if rates is None:
            raise RuntimeError(f"MT5 bars request failed: {mt5.last_error()}")
        n = 0 if rates is None else len(rates)
        print(f"  {tf} {a:%Y-%m}: {n:>7,d} bars", file=sys.stderr)
        if n:
            frames.append(pd.DataFrame(rates))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df["time"] = pd.to_datetime(df["time"], unit="s")          # documented API UTC; converted by main
    df = df.drop_duplicates("time").set_index("time").sort_index()
    df = df[(df.index >= start.replace(tzinfo=None)) & (df.index < end.replace(tzinfo=None))]
    return df[["open", "high", "low", "close", "tick_volume", "spread", "real_volume"]]


def fetch_ticks(mt5, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    frames = []
    day = start
    while day < end:
        nxt = min(day + timedelta(days=1), end)
        ticks = mt5.copy_ticks_range(symbol, day, nxt, mt5.COPY_TICKS_ALL)
        if ticks is None:
            raise RuntimeError(f"MT5 ticks request failed: {mt5.last_error()}")
        n = 0 if ticks is None else len(ticks)
        if n:
            chunk = pd.DataFrame(ticks)
            frames.append(chunk[(chunk.time_msc >= day.timestamp() * 1000) &
                                (chunk.time_msc < nxt.timestamp() * 1000)])
        if day.day == 1 or n == 0:
            print(f"  ticks {day:%Y-%m-%d}: {n:>9,d}", file=sys.stderr)
        day = nxt
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df["time"] = pd.to_datetime(df["time_msc"], unit="ms")
    df = df.set_index("time").sort_index()
    return df[["bid", "ask", "last", "volume", "flags"]]


def ticks_to_m1(ticks: pd.DataFrame, point: float) -> pd.DataFrame:
    """Bid OHLC per minute with the average spread of that minute, like MT5's own M1 bars."""
    t = ticks[(ticks["bid"] > 0) & (ticks["ask"] > 0)]
    g = t["bid"].resample("1min", label="left", closed="left")
    out = pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last()})
    out["tick_volume"] = g.count()
    out["spread"] = ((t["ask"] - t["bid"]) / point).resample("1min", label="left", closed="left").mean().round()
    out["real_volume"] = 0
    out = out.dropna(subset=["open"])
    out.index.name = "time"
    return out


def describe_symbol(mt5, symbol: str) -> None:
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"symbol {symbol} not found - check the exact name in Market Watch", file=sys.stderr)
        return
    point_value = info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else float("nan")
    print("\nSymbol details for the Python params:")
    print(f"  digits={info.digits}  point={info.point}  tick_size={info.trade_tick_size}  tick_value={info.trade_tick_value}")
    print(f"  contract_size={info.trade_contract_size}  volume_min={info.volume_min}  volume_step={info.volume_step}  volume_max={info.volume_max}")
    print(f"  current spread={info.spread} points  stops_level={info.trade_stops_level} points")
    print("\n  Params(point=%g, point_value=%g, lot_step=%g, min_lot=%g, max_lot=%g, spread=%g)"
          % (info.point, point_value, info.volume_step, info.volume_min, info.volume_max, info.spread * info.point))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True, help="exact symbol name from Market Watch, e.g. NAS100 or USTEC")
    ap.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="end", default=None, help="YYYY-MM-DD (default: now)")
    ap.add_argument("--tf", nargs="*", default=["M1", "M15"], choices=list(TF_NAMES), help="timeframes to export")
    ap.add_argument("--ticks", action="store_true", help="also export raw ticks and M1 bars rebuilt from them")
    ap.add_argument("--out", default="data", help="output folder")
    ap.add_argument("--tz", required=True, help="Explicit UTC -> broker clock: ny+7 | utc | +3; verify on chart")
    args = ap.parse_args()

    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError:
        print("pip install MetaTrader5   (Windows only, needs the terminal running)", file=sys.stderr)
        return 1

    if not mt5.initialize():
        print(f"MT5 initialize() failed: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        if not mt5.symbol_select(args.symbol, True):
            print(f"could not select {args.symbol}: {mt5.last_error()}", file=sys.stderr)
            return 1

        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
               if args.end else datetime.now(timezone.utc))
        if end <= start:
            raise ValueError("--to must be later than --from; end is exclusive")
        to_server_time(pd.DatetimeIndex([start]), args.tz)
        received = False
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)

        for tf in args.tf:
            print(f"{args.symbol} {tf} {start:%Y-%m-%d} .. {end:%Y-%m-%d}", file=sys.stderr)
            df = fetch_bars(mt5, args.symbol, tf, start, end)
            if df.empty:
                print(f"  no {tf} data returned", file=sys.stderr)
                continue
            received = True
            df.index = to_server_time(df.index, args.tz)
            path = out / f"{args.symbol}_{tf}.csv"
            df.to_csv(path)
            print(f"  -> {path}  ({len(df):,} bars, {df.index[0]} .. {df.index[-1]})", file=sys.stderr)

        if args.ticks:
            print(f"{args.symbol} ticks {start:%Y-%m-%d} .. {end:%Y-%m-%d} (this can take a while)", file=sys.stderr)
            ticks = fetch_ticks(mt5, args.symbol, start, end)
            if ticks.empty:
                print("  no ticks returned", file=sys.stderr)
            else:
                received = True
                ticks.index = to_server_time(ticks.index, args.tz)
                tpath = out / f"{args.symbol}_ticks.csv"
                ticks.to_csv(tpath)
                print(f"  -> {tpath}  ({len(ticks):,} ticks)", file=sys.stderr)
                info = mt5.symbol_info(args.symbol)
                m1 = ticks_to_m1(ticks, info.point)
                mpath = out / f"{args.symbol}_M1_from_ticks.csv"
                m1.to_csv(mpath)
                print(f"  -> {mpath}  ({len(m1):,} M1 bars with real per-minute spread)", file=sys.stderr)

        describe_symbol(mt5, args.symbol)
        if not received:
            print("No data returned. Increase Max bars in chart and check the requested dates.", file=sys.stderr)
            return 1
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
