#!/usr/bin/env python3
"""Build compact M1 QQQ bars on WRDS Cloud from TAQ trades and NBBO quotes."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SYMBOL_RE = re.compile(r"[A-Z0-9.]{1,16}")


def progress(done: int, total: int, label: str, rows: int, started: float,
             last_percent: int = -1) -> int:
    """Write at most one durable qsub-log line per percentage point."""
    percent = 100 if total == 0 else int(done * 100 / total)
    if percent == last_percent and done != total:
        return last_percent
    elapsed = time.monotonic() - started
    eta = elapsed * (total - done) / done if done else 0.0
    width = 30
    filled = width if total == 0 else int(width * done / total)
    bar = "#" * filled + "-" * (width - filled)
    eta_text = f" ETA {eta / 60:.1f}m" if done else ""
    print(f"[{bar}] {percent:3d}% {done}/{total} {label} | {rows:,} M1 rows | "
          f"{elapsed / 60:.1f}m{eta_text}", flush=True)
    return percent


def weekdays(start: date, end: date):
    current = start
    while current < end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def available_tables(db) -> set[str]:
    frame = db.raw_sql(
        """
        select table_name from information_schema.tables
        where table_schema = 'taqmsec'
          and (table_name like 'ctm_%%' or table_name like 'complete_nbbo_%%')
        """
    )
    return set(frame.table_name.astype(str))


def tables_with_column(db, column: str) -> set[str]:
    frame = db.raw_sql(
        """
        select table_name from information_schema.columns
        where table_schema = 'taqmsec' and column_name = %(column)s
          and table_name like 'complete_nbbo_%%'
        """,
        params={"column": column},
    )
    return set(frame.table_name.astype(str))


def query_day(db, day: date, symbol: str, start_time: str, end_time: str,
              point: float, strategy_offset_hours: int,
              quote_has_nanoseconds: bool) -> tuple[pd.DataFrame, dict]:
    suffix = day.strftime("%Y%m%d")
    trade_table = f"ctm_{suffix}"
    quote_table = f"complete_nbbo_{suffix}"
    ascending_order = "time_m, time_m_nano" if quote_has_nanoseconds else "time_m"
    descending_order = ("time_m desc, time_m_nano desc" if quote_has_nanoseconds
                        else "time_m desc")
    params = {"symbol": symbol, "start_time": start_time, "end_time": end_time}
    quotes = db.raw_sql(
        f"""
        select date_trunc('minute', date + time_m) as minute,
               (array_agg(best_bid order by {ascending_order}))[1] as open,
               max(best_bid) as high,
               min(best_bid) as low,
               (array_agg(best_bid order by {descending_order}))[1] as close,
               count(*) as quote_updates,
               percentile_cont(0.50) within group (order by best_ask-best_bid) as spread_median,
               percentile_cont(0.95) within group (order by best_ask-best_bid) as spread_p95
        from taqmsec.{quote_table}
        where sym_root = %(symbol)s and coalesce(sym_suffix, '') = ''
          and time_m >= cast(%(start_time)s as time) and time_m < cast(%(end_time)s as time)
          and best_bid > 0 and best_ask > 0 and best_ask >= best_bid
        group by 1 order by 1
        """,
        params=params,
    )
    trades = db.raw_sql(
        f"""
        select date_trunc('minute', date + time_m) as minute,
               count(*) as trade_count, sum(size) as share_volume
        from taqmsec.{trade_table}
        where sym_root = %(symbol)s and coalesce(sym_suffix, '') = ''
          and time_m >= cast(%(start_time)s as time) and time_m < cast(%(end_time)s as time)
          and tr_corr = '00' and price > 0 and size > 0
        group by 1 order by 1
        """,
        params=params,
    )
    if quotes.empty:
        return pd.DataFrame(), {"date": day.isoformat(), "reason": "no valid NBBO rows"}
    quotes = quotes.assign(minute=pd.to_datetime(quotes.minute))
    trades = trades.assign(minute=pd.to_datetime(trades.minute))
    bars = quotes.merge(trades, on="minute", how="left")
    bars = bars.assign(
        tick_volume=bars.trade_count.fillna(0).astype("int64"),
        real_volume=bars.share_volume.fillna(0).astype("int64"),
        spread=bars.spread_p95.map(
            lambda value: max(0, math.ceil(float(value) / point - 1e-9))),
        time=bars.minute + pd.Timedelta(hours=strategy_offset_hours),
    )
    bars = bars[["time", "open", "high", "low", "close", "tick_volume", "spread",
                 "real_volume", "quote_updates", "spread_median", "spread_p95"]]
    return bars, {
        "date": day.isoformat(),
        "minutes": len(bars),
        "trade_count": int(bars.tick_volume.sum()),
        "quote_updates": int(bars.quote_updates.sum()),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True,
                        help="End-exclusive date")
    parser.add_argument("--start-time", default="07:30:00",
                        help="New York start; 07:30 supplies two hours before the 09:30 range")
    parser.add_argument("--end-time", default="16:01:00")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--strategy-offset-hours", type=int, default=7,
                        help="Add to New York wall time; 7 maps 09:30 to strategy 16:30")
    parser.add_argument("--output-dir", type=Path, default=Path("orb_m1"))
    args = parser.parse_args(argv)
    args.symbol = args.symbol.upper()
    if not SYMBOL_RE.fullmatch(args.symbol):
        parser.error("invalid symbol")
    if args.end <= args.start:
        parser.error("--to must be later than --from")
    if not math.isfinite(args.point) or args.point <= 0:
        parser.error("--point must be positive")
    for value in (args.start_time, args.end_time):
        try:
            datetime.strptime(value, "%H:%M:%S")
        except ValueError:
            parser.error("times must use HH:MM:SS")
    return args


def main() -> int:
    args = parse_args()
    try:
        import wrds
    except ImportError as exc:
        raise SystemExit("The WRDS Python package is unavailable on this compute node") from exc
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records, missing, frames = [], [], []
    requested_days = list(weekdays(args.start, args.end))
    started = time.monotonic()
    last_percent = progress(0, len(requested_days), "starting", 0, started)
    with wrds.Connection(wrds_username=getpass.getuser()) as db:
        tables = available_tables(db)
        nanosecond_tables = tables_with_column(db, "time_m_nano")
        for position, day in enumerate(requested_days, start=1):
            required = {f"ctm_{day:%Y%m%d}", f"complete_nbbo_{day:%Y%m%d}"}
            if not required <= tables:
                missing.append({"date": day.isoformat(), "reason": "market holiday or unavailable table"})
            else:
                bars, record = query_day(db, day, args.symbol, args.start_time, args.end_time,
                                         args.point, args.strategy_offset_hours,
                                         f"complete_nbbo_{day:%Y%m%d}" in nanosecond_tables)
                if bars.empty:
                    missing.append(record)
                else:
                    frames.append(bars)
                    records.append(record)
            last_percent = progress(position, len(requested_days), day.isoformat(),
                                    sum(len(frame) for frame in frames), started, last_percent)
    if not frames:
        raise SystemExit("No M1 bars produced")
    result = pd.concat(frames, ignore_index=True).sort_values("time")
    if result.time.duplicated().any():
        raise SystemExit("Duplicate strategy-clock timestamps produced")
    output = args.output_dir / f"{args.symbol}_{args.start}_{args.end}_M1.csv.gz"
    result.to_csv(output, index=False, compression="gzip", float_format="%.8f")
    manifest = {
        "provider": "WRDS NYSE TAQ Daily Product",
        "schema": "taqmsec",
        "symbol": args.symbol,
        "start": args.start.isoformat(),
        "end_exclusive": args.end.isoformat(),
        "source_start_time": args.start_time,
        "source_end_time_exclusive": args.end_time,
        "source_clock": "America/New_York wall time",
        "strategy_clock": f"New York wall time + {args.strategy_offset_hours} hours",
        "price_basis": "valid non-crossed consolidated NBBO best bid",
        "quote_ordering": "time_m then time_m_nano when the daily table provides nanoseconds",
        "spread_basis": "within-minute p95 NBBO spread, stored in point units",
        "activity_basis": "positive, correction-code 00 consolidated trades",
        "point": args.point,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "file": output.name,
        "sha256": sha256(output),
        "rows": len(result),
        "days": records,
        "nontrading_or_missing_days": missing,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Data: {output} ({len(result):,} rows)")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
