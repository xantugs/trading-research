#!/usr/bin/env python3
"""Convert local WRDS QQQ complete-NBBO Parquet into MT5 custom-symbol ticks.

The converter keeps the full 00:00-24:00 New York source day. Invalid and
crossed quotes are removed. Consecutive rows with the same bid and ask are
removed by default because they change metadata, not the executable price path.
Every price change is retained, including multiple changes in one millisecond.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from orb.ticks import TickStore

MT5_COLUMNS = ["<DATE>", "<TIME>", "<BID>", "<ASK>", "<LAST>", "<VOLUME>"]
QUOTE_NAME = re.compile(r"^(?P<symbol>[A-Z0-9.]+)_(?P<date>\d{4}-\d{2}-\d{2})_quotes\.parquet$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_quote_days(root: Path, symbol: str, start: date, end: date) -> list[tuple[date, Path]]:
    found: dict[date, Path] = {}
    for path in root.glob("*/*/*_quotes.parquet"):
        match = QUOTE_NAME.fullmatch(path.name)
        if not match or match.group("symbol") != symbol:
            continue
        day = date.fromisoformat(match.group("date"))
        if not start <= day < end:
            continue
        if day in found:
            raise ValueError(f"More than one {symbol} quote file for {day}: {found[day]} and {path}")
        found[day] = path
    if not found:
        raise ValueError(f"No {symbol} quote Parquet files under {root} in [{start}, {end})")
    return sorted(found.items())


def price_change_mask(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """Keep the first state and every subsequent executable price change."""
    if not len(bid):
        return np.zeros(0, dtype=bool)
    return np.r_[True, (bid[1:] != bid[:-1]) | (ask[1:] != ask[:-1])]


def _validate_price_grid(values: np.ndarray, digits: int, label: str) -> None:
    scale = 10 ** digits
    error = np.abs(values * scale - np.rint(values * scale))
    bad = np.flatnonzero(error > 1e-7)
    if len(bad):
        value = values[int(bad[0])]
        raise ValueError(
            f"{label} {value!r} does not fit --digits {digits}; increase digits to avoid rounding"
        )


def write_mt5_csv(path: Path, times: np.ndarray, bid: np.ndarray, ask: np.ndarray,
                  digits: int, chunk_rows: int) -> dict:
    """Write MT5's tab-delimited tick-import format and validate ordering."""
    _validate_price_grid(bid, digits, "Bid")
    _validate_price_grid(ask, digits, "Ask")
    milliseconds = times.astype("datetime64[ms]")
    if len(milliseconds) > 1 and np.any(milliseconds[1:] < milliseconds[:-1]):
        raise ValueError("Millisecond timestamps are not nondecreasing")
    staging = path.with_name(f".{path.name}.partial")
    staging.unlink(missing_ok=True)
    formatter = f"%.{digits}f"
    try:
        with staging.open("w", encoding="utf-8", newline="") as handle:
            for left in range(0, len(milliseconds), chunk_rows):
                right = min(left + chunk_rows, len(milliseconds))
                stamp = pd.DatetimeIndex(milliseconds[left:right])
                frame = pd.DataFrame({
                    "<DATE>": stamp.strftime("%Y.%m.%d"),
                    "<TIME>": stamp.strftime("%H:%M:%S.%f").str[:-3],
                    "<BID>": bid[left:right],
                    "<ASK>": ask[left:right],
                    "<LAST>": np.zeros(right - left, dtype=np.float64),
                    "<VOLUME>": np.zeros(right - left, dtype=np.int64),
                })
                frame.to_csv(
                    handle, sep="\t", index=False, header=left == 0,
                    float_format=formatter, lineterminator="\n",
                )
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
    adjacent_same_ms = int(np.sum(milliseconds[1:] == milliseconds[:-1])) if len(milliseconds) > 1 else 0
    return {
        "rows": len(milliseconds),
        "first": str(pd.Timestamp(milliseconds[0])) if len(milliseconds) else None,
        "last": str(pd.Timestamp(milliseconds[-1])) if len(milliseconds) else None,
        "adjacent_same_millisecond_rows": adjacent_same_ms,
    }


def verify_mt5_csv(path: Path, *, expected_rows: int | None = None,
                   price_changes_only: bool = True, chunk_rows: int = 250_000) -> dict:
    """Stream-verify schema, prices, ordering, and optional de-duplication."""
    rows = 0
    previous_time = None
    previous_bid = None
    previous_ask = None
    first = None
    last = None
    same_ms = 0
    for frame in pd.read_csv(path, sep="\t", chunksize=chunk_rows, dtype={
        "<DATE>": "string", "<TIME>": "string", "<VOLUME>": "int64",
    }):
        if list(frame.columns) != MT5_COLUMNS:
            raise ValueError(f"{path}: expected MT5 columns {MT5_COLUMNS}, found {list(frame.columns)}")
        stamp = pd.to_datetime(
            frame["<DATE>"] + " " + frame["<TIME>"],
            format="%Y.%m.%d %H:%M:%S.%f", errors="raise",
        ).to_numpy(dtype="datetime64[ms]")
        bid = pd.to_numeric(frame["<BID>"], errors="raise").to_numpy(dtype=np.float64)
        ask = pd.to_numeric(frame["<ASK>"], errors="raise").to_numpy(dtype=np.float64)
        if not (np.isfinite(bid).all() and np.isfinite(ask).all()
                and (bid > 0).all() and (ask >= bid).all()):
            raise ValueError(f"{path}: invalid, nonpositive, or crossed bid/ask")
        if not (frame["<LAST>"] == 0).all() or not (frame["<VOLUME>"] == 0).all():
            raise ValueError(f"{path}: quote-only ticks require LAST=0 and VOLUME=0")
        if len(stamp) > 1 and np.any(stamp[1:] < stamp[:-1]):
            raise ValueError(f"{path}: timestamps decrease inside a chunk")
        if previous_time is not None:
            if stamp[0] < previous_time:
                raise ValueError(f"{path}: timestamps decrease between chunks")
            if stamp[0] == previous_time:
                same_ms += 1
            if price_changes_only and bid[0] == previous_bid and ask[0] == previous_ask:
                raise ValueError(f"{path}: consecutive duplicate bid/ask state")
        if price_changes_only and len(bid) > 1:
            duplicate = (bid[1:] == bid[:-1]) & (ask[1:] == ask[:-1])
            if duplicate.any():
                raise ValueError(f"{path}: consecutive duplicate bid/ask state")
        if len(stamp) > 1:
            same_ms += int(np.sum(stamp[1:] == stamp[:-1]))
        if first is None and len(stamp):
            first = str(pd.Timestamp(stamp[0]))
        if len(stamp):
            last = str(pd.Timestamp(stamp[-1]))
            previous_time = stamp[-1]
            previous_bid = bid[-1]
            previous_ask = ask[-1]
        rows += len(frame)
    if rows == 0:
        raise ValueError(f"{path}: no tick rows")
    if expected_rows is not None and rows != expected_rows:
        raise ValueError(f"{path}: found {rows:,} rows, expected {expected_rows:,}")
    return {"rows": rows, "first": first, "last": last,
            "adjacent_same_millisecond_rows": same_ms}


def convert_day(store: TickStore, day: date, output_dir: Path, *, digits: int,
                chunk_rows: int, keep_unchanged: bool, overwrite: bool) -> dict:
    quotes = store.load_day(day)
    mask = (np.ones(len(quotes), dtype=bool) if keep_unchanged
            else price_change_mask(quotes.bid, quotes.ask))
    times = quotes.time[mask]
    bid = quotes.bid[mask]
    ask = quotes.ask[mask]
    target = output_dir / f"{store.symbol}_{day.isoformat()}_ticks.csv"
    manifest_path = target.with_suffix(".manifest.json")
    if (target.exists() or manifest_path.exists()) and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {target}; pass --overwrite")
    output_dir.mkdir(parents=True, exist_ok=True)
    written = write_mt5_csv(target, times, bid, ask, digits, chunk_rows)
    verified = verify_mt5_csv(
        target, expected_rows=written["rows"],
        price_changes_only=not keep_unchanged, chunk_rows=chunk_rows,
    )
    if written != verified:
        raise ValueError(f"{target}: write/verify statistics disagree")
    source = quotes.coverage
    manifest = {
        "format_version": 1,
        "provider": "WRDS NYSE TAQ Daily Product",
        "source": "complete NBBO",
        "symbol": store.symbol,
        "source_date": day.isoformat(),
        "clock": "America/New_York wall time; no strategy offset",
        "window": "00:00:00.000 <= time < next-day 00:00:00.000",
        "precision": "milliseconds; original nanosecond order retained for equal millisecond rows",
        "cleaning": (
            "invalid/crossed quotes removed; consecutive unchanged bid/ask states retained"
            if keep_unchanged else
            "invalid/crossed quotes removed; consecutive unchanged bid/ask states removed"
        ),
        "mt5_columns": MT5_COLUMNS,
        "price_digits": digits,
        "source_file": str(source.source_path.resolve()),
        "source_size_bytes": source.source_path.stat().st_size,
        "source_rows": source.source_rows,
        "valid_nbbo_rows": source.rows,
        "dropped_timestamps": source.dropped_timestamps,
        "dropped_invalid": source.dropped_invalid,
        "dropped_crossed": source.dropped_crossed,
        "removed_unchanged_price_states": int(source.rows - len(times)),
        "output_file": target.name,
        "output_size_bytes": target.stat().st_size,
        "output_sha256": sha256(target),
        **written,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=Path(r"C:\data\wrds_ticks"))
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True,
                        help="End-exclusive New York source date")
    parser.add_argument("--output-dir", type=Path, default=Path(r"C:\data\mt5_qqq_ticks"))
    parser.add_argument("--digits", type=int, default=2)
    parser.add_argument("--chunk-rows", type=int, default=250_000)
    parser.add_argument("--keep-unchanged", action="store_true",
                        help="Keep metadata-only quote updates whose bid and ask did not change")
    parser.add_argument("--allow-large-range", action="store_true",
                        help="Allow more than 31 source days in one command")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    args.symbol = args.symbol.strip().upper()
    if args.end <= args.start:
        parser.error("--to must be later than --from")
    if not re.fullmatch(r"[A-Z0-9.]{1,16}", args.symbol):
        parser.error("invalid symbol")
    if not 0 <= args.digits <= 8:
        parser.error("--digits must be between 0 and 8")
    if args.chunk_rows < 1:
        parser.error("--chunk-rows must be positive")
    if (args.end - args.start).days > 31 and not args.allow_large_range:
        parser.error("date range exceeds 31 days; process one month at a time or pass --allow-large-range")
    return args


def progress(done: int, total: int, label: str, started: float) -> None:
    elapsed = time.monotonic() - started
    width = 30
    filled = int(width * done / total)
    print(f"[{'#' * filled}{'-' * (width - filled)}] {done / total:6.1%} "
          f"{done}/{total} {label} | {elapsed / 60:.1f}m", flush=True)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.root.is_dir():
        print(f"No WRDS tick root: {args.root}", file=sys.stderr)
        return 1
    try:
        days = discover_quote_days(args.root, args.symbol, args.start, args.end)
        store = TickStore(args.root, strategy_offset_hours=0, symbol=args.symbol, max_cache_days=0)
        started = time.monotonic()
        progress(0, len(days), "starting", started)
        manifests = []
        for position, (day, _) in enumerate(days, start=1):
            result = convert_day(
                store, day, args.output_dir, digits=args.digits,
                chunk_rows=args.chunk_rows, keep_unchanged=args.keep_unchanged,
                overwrite=args.overwrite,
            )
            manifests.append(result)
            progress(position, len(days),
                     f"{day} ({result['rows']:,} MT5 ticks)", started)
        total_source = sum(item["source_rows"] for item in manifests)
        total_output = sum(item["rows"] for item in manifests)
        print(f"Ready: {len(manifests)} day(s), {total_output:,} MT5 ticks from "
              f"{total_source:,} source rows ({total_source / total_output:.1f}x fewer rows)")
        print(f"Files: {args.output_dir.resolve()}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
