#!/usr/bin/env python3
"""Build local QQQ M1 research bars from downloaded WRDS TAQ Parquet files.

The raw tree is expected to contain one ``*_trades.parquet`` and one
``*_quotes.parquet`` file per trading day.  Output is written as both monthly
and yearly gzip CSV files, each with a provenance manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SYMBOL_RE = re.compile(r"[A-Z0-9.]{1,16}")
RAW_FILE_RE = re.compile(
    r"(?P<symbol>[A-Z0-9.]+)_(?P<day>\d{4}-\d{2}-\d{2})_(?P<kind>trades|quotes)\.parquet$"
)
OUTPUT_COLUMNS = [
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
    "quote_updates",
    "spread_median",
    "spread_p95",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wall_time(value: str, *, end: bool = False) -> pd.Timedelta:
    if end and value == "24:00:00":
        return pd.Timedelta(days=1)
    try:
        parsed = datetime.strptime(value, "%H:%M:%S")
    except ValueError as exc:
        suffix = " or 24:00:00" if end else ""
        raise ValueError(f"time must use HH:MM:SS{suffix}: {value}") from exc
    return pd.Timedelta(hours=parsed.hour, minutes=parsed.minute, seconds=parsed.second)


def discover_pairs(root: Path, symbol: str, start: date | None = None,
                   end: date | None = None) -> dict[date, dict[str, Path]]:
    """Find complete daily trade/quote pairs and reject duplicate or partial days."""
    pairs: dict[date, dict[str, Path]] = defaultdict(dict)
    for path in root.rglob("*.parquet"):
        match = RAW_FILE_RE.fullmatch(path.name)
        if not match or match.group("symbol") != symbol:
            continue
        day = date.fromisoformat(match.group("day"))
        if start is not None and day < start:
            continue
        if end is not None and day >= end:
            continue
        kind = match.group("kind")
        if kind in pairs[day]:
            raise ValueError(f"Duplicate {kind} file for {day}: {pairs[day][kind]} and {path}")
        pairs[day][kind] = path
    if not pairs:
        raise ValueError(f"No {symbol} daily trade/quote Parquet files found under {root}")
    incomplete = {day: sorted(set(("trades", "quotes")) - set(found))
                  for day, found in pairs.items() if set(found) != {"trades", "quotes"}}
    if incomplete:
        details = ", ".join(f"{day}: missing {'/'.join(kinds)}" for day, kinds in sorted(incomplete.items()))
        raise ValueError(f"Incomplete WRDS daily pairs: {details}")
    return dict(sorted(pairs.items()))


def _require_columns(path: Path, required: set[str]) -> list[str]:
    import pyarrow.parquet as pq

    available = set(pq.ParquetFile(path).schema.names)
    missing = required - available
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    optional = [name for name in ("time_m_nano", "tr_seqnum") if name in available]
    return sorted(required) + optional


def _time_fields(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    elapsed = pd.to_timedelta(frame["time_m"].astype(str), errors="coerce")
    valid = elapsed.notna()
    frame = frame.loc[valid].copy()
    elapsed = elapsed.loc[valid]
    frame["_elapsed"] = elapsed
    frame["_minute"] = pd.Timestamp(day) + elapsed.dt.floor("min")
    frame["_order"] = elapsed.astype("timedelta64[ns]").astype("int64")
    if "time_m_nano" in frame:
        nano = pd.to_numeric(frame["time_m_nano"], errors="coerce").fillna(0).astype("int64")
        frame["_order"] += nano
    return frame


def aggregate_trades(path: Path, day: date, start_time: pd.Timedelta,
                     end_time: pd.Timedelta) -> pd.DataFrame:
    """Count eligible consolidated trades and sum their share volume."""
    import pyarrow.parquet as pq

    required = {"time_m", "price", "size", "tr_corr"}
    columns = _require_columns(path, required)
    parts = []
    for batch in pq.ParquetFile(path).iter_batches(batch_size=250_000, columns=columns):
        frame = _time_fields(batch.to_pandas(), day)
        price = pd.to_numeric(frame["price"], errors="coerce")
        size = pd.to_numeric(frame["size"], errors="coerce")
        correction = frame["tr_corr"].astype("string").fillna("").str.strip()
        keep = (
            (frame["_elapsed"] >= start_time)
            & (frame["_elapsed"] < end_time)
            & np.isfinite(price)
            & np.isfinite(size)
            & (price > 0)
            & (size > 0)
            & correction.eq("00")
        )
        if keep.any():
            eligible = pd.DataFrame({
                "_minute": frame.loc[keep, "_minute"].to_numpy(),
                "size": size.loc[keep].to_numpy(dtype="int64"),
            })
            parts.append(eligible.groupby("_minute", sort=False).agg(
                trade_count=("size", "size"), real_volume=("size", "sum")
            ))
    if not parts:
        return pd.DataFrame(columns=["trade_count", "real_volume"])
    return pd.concat(parts).groupby(level=0, sort=True).sum().astype("int64")


def aggregate_quotes(path: Path, day: date, start_time: pd.Timedelta,
                     end_time: pd.Timedelta) -> pd.DataFrame:
    """Aggregate valid, non-crossed complete-NBBO updates and spread percentiles."""
    import pyarrow.parquet as pq

    required = {"time_m", "best_bid", "best_ask"}
    columns = _require_columns(path, required)
    minute_parts: list[np.ndarray] = []
    order_parts: list[np.ndarray] = []
    bid_parts: list[np.ndarray] = []
    ask_parts: list[np.ndarray] = []
    spread_parts: list[np.ndarray] = []
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=250_000, columns=columns):
        frame = _time_fields(batch.to_pandas(), day)
        bid = pd.to_numeric(frame["best_bid"], errors="coerce")
        ask = pd.to_numeric(frame["best_ask"], errors="coerce")
        keep = (
            (frame["_elapsed"] >= start_time)
            & (frame["_elapsed"] < end_time)
            & np.isfinite(bid)
            & np.isfinite(ask)
            & (bid > 0)
            & (ask > 0)
            & (ask >= bid)
        )
        if not keep.any():
            continue
        minute_parts.append(frame.loc[keep, "_minute"].to_numpy())
        order_parts.append(frame.loc[keep, "_order"].to_numpy(dtype="int64"))
        bid_parts.append(bid.loc[keep].to_numpy(dtype=float))
        ask_parts.append(ask.loc[keep].to_numpy(dtype=float))
        spread_parts.append((ask.loc[keep] - bid.loc[keep]).to_numpy(dtype=float))
    if not spread_parts:
        return pd.DataFrame()
    minutes = np.concatenate(minute_parts).astype("datetime64[ns]")
    event_order = np.concatenate(order_parts)
    bids = np.concatenate(bid_parts)
    asks = np.concatenate(ask_parts)
    values = np.concatenate(spread_parts)
    order = np.lexsort((event_order, minutes.astype("int64")))
    minutes, bids, asks, values = minutes[order], bids[order], asks[order], values[order]
    unique, starts, counts = np.unique(minutes, return_index=True, return_counts=True)
    opening = bids[starts]
    closing = bids[starts + counts - 1]
    high = np.maximum.reduceat(bids, starts)
    low = np.minimum.reduceat(bids, starts)
    median = np.empty(len(unique), dtype=float)
    p95 = np.empty(len(unique), dtype=float)
    for position, (start, count) in enumerate(zip(starts, counts)):
        median[position], p95[position] = np.percentile(values[start:start + count], (50, 95))
    return pd.DataFrame({
        "open": opening,
        "high": high,
        "low": low,
        "close": closing,
        "quote_updates": counts,
        "spread_median": median,
        "spread_p95": p95,
    }, index=pd.DatetimeIndex(unique, name="_minute"))


def build_day(trade_path: Path, quote_path: Path, day: date, point: float,
              strategy_offset_hours: int = 7, start_time: str = "04:00:00",
              end_time: str = "20:00:00") -> tuple[pd.DataFrame, dict]:
    """Build one day and return canonical-plus-diagnostic M1 rows and provenance."""
    start_delta = _wall_time(start_time)
    end_delta = _wall_time(end_time, end=True)
    if end_delta <= start_delta:
        raise ValueError("end time must be later than start time")
    quotes = aggregate_quotes(quote_path, day, start_delta, end_delta)
    trades = aggregate_trades(trade_path, day, start_delta, end_delta)
    if quotes.empty:
        raise ValueError(f"{day}: no valid NBBO quotes in the selected session")
    trade_minutes = set(trades.index)
    quote_minutes = set(quotes.index)
    bars = quotes.join(trades, how="left")
    bars[["trade_count", "real_volume"]] = bars[["trade_count", "real_volume"]].fillna(0).astype("int64")
    bars["tick_volume"] = bars["trade_count"]
    bars["spread"] = bars["spread_p95"].map(
        lambda value: max(0, math.ceil(float(value) / point - 1e-9))
    ).astype("int64")
    bars.index = bars.index + pd.Timedelta(hours=strategy_offset_hours)
    bars.index.name = "time"
    bars = bars.reset_index()[OUTPUT_COLUMNS]
    record = {
        "date": day.isoformat(),
        "rows": len(bars),
        "first": str(bars.time.iloc[0]),
        "last": str(bars.time.iloc[-1]),
        "trade_count": int(bars.tick_volume.sum()),
        "share_volume": int(bars.real_volume.sum()),
        "quote_updates": int(bars.quote_updates.sum()),
        "trade_only_minutes_dropped": len(trade_minutes - quote_minutes),
        "quote_only_minutes": len(quote_minutes - trade_minutes),
        "source_files": [str(trade_path), str(quote_path)],
    }
    return bars, record


def _write_csv(frame: pd.DataFrame, path: Path, force: bool) -> dict:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite {path}; pass --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0}, float_format="%.8f")
    return {"file": path.name, "rows": len(frame), "sha256": sha256(path)}


def _write_json(value: dict, path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite {path}; pass --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build(root: Path, output_dir: Path, symbol: str = "QQQ", start: date | None = None,
          end: date | None = None, start_time: str = "04:00:00", end_time: str = "20:00:00",
          point: float = 0.01, strategy_offset_hours: int = 7, force: bool = False) -> dict:
    """Build all selected days, monthly outputs, yearly outputs, and manifests."""
    pairs = discover_pairs(root, symbol, start, end)
    grouped: dict[tuple[int, int], list[tuple[date, dict[str, Path]]]] = defaultdict(list)
    for day, paths in pairs.items():
        grouped[(day.year, day.month)].append((day, paths))

    created = datetime.now(timezone.utc).isoformat()
    common = {
        "provider": "WRDS NYSE TAQ Millisecond Daily Product",
        "schema": "taqmsec",
        "symbol": symbol,
        "source_clock": "America/New_York wall time",
        "strategy_clock": f"New York wall time + {strategy_offset_hours} hours",
        "source_start_time": start_time,
        "source_end_time_exclusive": end_time,
        "price_basis": "valid non-crossed complete-NBBO best bid",
        "quote_ordering": "time_m then time_m_nano when present",
        "tick_volume_basis": "positive correction-code 00 consolidated trade count",
        "spread_basis": "within-minute p50 and p95 NBBO spread; canonical spread is p95 in point units",
        "real_volume_basis": "share size of eligible consolidated trades",
        "point": point,
        "created_utc": created,
    }
    months_by_year: dict[int, list[tuple[pd.DataFrame, dict]]] = defaultdict(list)
    total = len(pairs)
    done = 0
    rows_done = 0
    started = time.monotonic()
    last_percent = -1
    for (year, month), entries in sorted(grouped.items()):
        frames, days = [], []
        for day, paths in entries:
            frame, record = build_day(paths["trades"], paths["quotes"], day, point,
                                      strategy_offset_hours, start_time, end_time)
            frames.append(frame)
            days.append(record)
            done += 1
            rows_done += len(frame)
            elapsed = time.monotonic() - started
            eta = elapsed * (total - done) / done
            percent = int(done * 100 / total)
            if percent != last_percent or done == total:
                filled = int(30 * done / total)
                bar = "#" * filled + "-" * (30 - filled)
                print(f"[{bar}] {percent:3d}% {done}/{total} {day} | "
                      f"{rows_done:,} M1 rows | "
                      f"{elapsed / 60:.1f}m ETA {eta / 60:.1f}m", flush=True)
                last_percent = percent
        month_frame = pd.concat(frames, ignore_index=True).sort_values("time")
        month_name = f"{year:04d}-{month:02d}"
        month_dir = output_dir / "monthly" / str(year) / month_name
        month_file = month_dir / f"{symbol}_{month_name}_M1.csv.gz"
        artifact = _write_csv(month_frame, month_file, force)
        manifest = {
            **common,
            "period": "month",
            "start": min(day for day, _ in entries).isoformat(),
            "end_inclusive": max(day for day, _ in entries).isoformat(),
            **artifact,
            "days": days,
        }
        _write_json(manifest, month_dir / "manifest.json", force)
        months_by_year[year].append((month_frame, manifest))

    year_manifests = []
    for year, monthly in sorted(months_by_year.items()):
        year_frame = pd.concat([frame for frame, _ in monthly], ignore_index=True).sort_values("time")
        if year_frame.time.duplicated().any():
            raise ValueError(f"Duplicate M1 timestamps produced for {year}")
        year_dir = output_dir / "years" / str(year)
        year_file = year_dir / f"{symbol}_{year}-01-01_{year + 1}-01-01_M1.csv.gz"
        artifact = _write_csv(year_frame, year_file, force)
        year_manifest = {
            **common,
            "period": "year",
            "start": str(year_frame.time.min().date()),
            "end_inclusive": str(year_frame.time.max().date()),
            **artifact,
            "months": [manifest["file"] for _, manifest in monthly],
            "days": [day for _, manifest in monthly for day in manifest["days"]],
        }
        _write_json(year_manifest, year_dir / "manifest.json", force)
        year_manifests.append(year_manifest)

    top = {
        **common,
        "period": "collection",
        "source_root": str(root.resolve()),
        "output_root": str(output_dir.resolve()),
        "years": [{"year": int(item["start"][:4]), "file": item["file"],
                   "rows": item["rows"], "sha256": item["sha256"]}
                  for item in year_manifests],
        "months": len(grouped),
        "trading_days": total,
        "rows": sum(item["rows"] for item in year_manifests),
    }
    _write_json(top, output_dir / "manifest.json", force)
    return top


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Root of downloaded daily WRDS Parquet files")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="End-exclusive date")
    parser.add_argument("--start-time", default="04:00:00")
    parser.add_argument("--end-time", default="20:00:00")
    parser.add_argument("--point", type=float, default=0.01)
    parser.add_argument("--strategy-offset-hours", type=int, default=7)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    args.symbol = args.symbol.upper()
    if not SYMBOL_RE.fullmatch(args.symbol):
        parser.error("invalid symbol")
    if not args.root.is_dir():
        parser.error(f"raw root does not exist: {args.root}")
    if args.start and args.end and args.end <= args.start:
        parser.error("--to must be later than --from")
    if not math.isfinite(args.point) or args.point <= 0:
        parser.error("--point must be positive")
    try:
        _wall_time(args.start_time)
        end_delta = _wall_time(args.end_time, end=True)
        if end_delta <= _wall_time(args.start_time):
            raise ValueError("end time must be later than start time")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        import pyarrow  # noqa: F401
    except ImportError as exc:
        raise SystemExit("pyarrow is required; install the project app dependencies") from exc
    result = build(args.root, args.output_dir, args.symbol, args.start, args.end,
                   args.start_time, args.end_time, args.point,
                   args.strategy_offset_hours, args.force)
    print(f"Built {result['rows']:,} M1 rows across {result['trading_days']} trading days")
    print(f"Manifest: {args.output_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
