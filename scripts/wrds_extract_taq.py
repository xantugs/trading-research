#!/usr/bin/env python3
"""Extract a small TAQ subset while running on WRDS Cloud.

This file is uploaded to a user's WRDS home directory, submitted through qsub,
and writes zstd Parquet (or gzip CSV) files for later transfer. It never stores
or accepts a WRDS password.
"""
from __future__ import annotations

import argparse
import getpass
import gzip
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SYMBOL_RE = re.compile(r"[A-Z0-9.]{1,16}")
TABLE_PREFIXES = {
    "trades": ("ctm_",),
    "quotes": ("complete_nbbo_", "cqm_"),
}


def progress(done: int, total: int, label: str, rows: int, started: float,
             last_percent: int = -1) -> int:
    """Write compact progress milestones that remain readable in qsub logs."""
    percent = 100 if total == 0 else int(done * 100 / total)
    if percent == last_percent and done != total:
        return last_percent
    elapsed = time.monotonic() - started
    eta = elapsed * (total - done) / done if done else 0.0
    width = 30
    filled = width if total == 0 else int(width * done / total)
    bar = "#" * filled + "-" * (width - filled)
    eta_text = f" ETA {eta / 60:.1f}m" if done else ""
    print(f"[{bar}] {percent:3d}% {done}/{total} {label} | {rows:,} rows | "
          f"{elapsed / 60:.1f}m{eta_text}", flush=True)
    return percent


def dates(start: date, end: date):
    current = start
    while current < end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def table_candidates(day: date, kind: str) -> tuple[str, ...]:
    suffix = day.strftime("%Y%m%d")
    return tuple(prefix + suffix for prefix in TABLE_PREFIXES[kind])


def available_tables(db, candidates: tuple[str, ...]) -> set[str]:
    frame = db.raw_sql(
        """
        select table_name
        from information_schema.tables
        where table_schema = 'taqmsec' and table_name = any(%(tables)s)
        """,
        params={"tables": list(candidates)},
    )
    return set(frame["table_name"].astype(str))


def write_parquet(chunks, destination: Path, compression_level: int) -> int:
    """Stream DataFrame chunks into one zstd Parquet file with a stable schema."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on the compute node
        raise SystemExit(
            "pyarrow is required for --format parquet on this node; rerun with --format csv"
        ) from exc

    count = 0
    writer = None
    try:
        for chunk in chunks:
            if chunk.empty:
                continue
            arrow = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(destination, arrow.schema, compression="zstd",
                                          compression_level=compression_level)
            else:
                # Later chunks can infer a wider/narrower dtype (an all-null column,
                # say); cast to the first chunk's schema so the file stays readable.
                arrow = arrow.cast(writer.schema)
            writer.write_table(arrow)
            count += len(chunk)
    finally:
        if writer is not None:
            writer.close()
    return count


def write_csv_gz(chunks, destination: Path) -> int:
    count = 0
    wrote_header = False
    with gzip.open(destination, "wt", encoding="utf-8", newline="") as handle:
        for chunk in chunks:
            chunk.to_csv(handle, index=False, header=not wrote_header)
            wrote_header = True
            count += len(chunk)
    return count


def export_table(db, table: str, symbol: str, start_time: str, end_time: str,
                 destination: Path, chunk_size: int, fmt: str = "parquet",
                 compression_level: int = 9) -> int:
    columns = db.raw_sql(
        """
        select column_name from information_schema.columns
        where table_schema = 'taqmsec' and table_name = %(table)s
        """,
        params={"table": table},
    )["column_name"].astype(str).tolist()
    order = ["time_m"]
    if "time_m_nano" in columns:
        order.append("time_m_nano")
    sequence = next((name for name in ("tr_seqnum", "qu_seqnum", "seqnum") if name in columns), None)
    if sequence:
        order.append(sequence)
    end_clause = "" if end_time == "24:00:00" else "and time_m < cast(%(end_time)s as time)"
    sql = f"""
        select *
        from taqmsec.{table}
        where sym_root = %(symbol)s
          and coalesce(sym_suffix, '') = ''
          and time_m >= cast(%(start_time)s as time)
          {end_clause}
        order by {', '.join(order)}
    """
    count = 0
    try:
        result = db.raw_sql(
            sql,
            params={"symbol": symbol, "start_time": start_time, "end_time": end_time},
            chunksize=chunk_size,
        )
        # Current WRDS builds may ignore chunksize and return one DataFrame;
        # older builds return an iterator of DataFrames.
        chunks = (result,) if hasattr(result, "to_csv") else result
        if fmt == "csv":
            count = write_csv_gz(chunks, destination)
        else:
            count = write_parquet(chunks, destination, compression_level)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if count == 0:
        # A header-only or empty artifact is misleading; the caller records the
        # day under manifest "missing_tables" instead.
        destination.unlink(missing_ok=True)
    return count


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True,
                        help="End-exclusive date")
    parser.add_argument("--start-time", default="07:30:00",
                        help="New York start; 07:30 supplies two hours before the 09:30 range")
    parser.add_argument("--end-time", default="16:01:00")
    parser.add_argument("--output-dir", type=Path, default=Path("orb_exports"))
    parser.add_argument("--kind", choices=["trades", "quotes", "both"], default="both")
    parser.add_argument("--chunk-size", type=int, default=250_000)
    parser.add_argument("--format", choices=["parquet", "csv"], default="parquet",
                        help="parquet+zstd is roughly 3-4x smaller than gzip CSV for TAQ")
    parser.add_argument("--compression-level", type=int, default=9,
                        help="zstd level for --format parquet")
    args = parser.parse_args(argv)
    args.symbol = args.symbol.upper()
    if not SYMBOL_RE.fullmatch(args.symbol):
        parser.error("--symbol must contain only A-Z, 0-9 or periods")
    if args.end <= args.start:
        parser.error("--to must be later than --from")
    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive")
    for value in (args.start_time,):
        try:
            datetime.strptime(value, "%H:%M:%S")
        except ValueError:
            parser.error("times must use HH:MM:SS")
    if args.end_time != "24:00:00":
        try:
            datetime.strptime(args.end_time, "%H:%M:%S")
        except ValueError:
            parser.error("--end-time must use HH:MM:SS or 24:00:00")
    return args


def main() -> int:
    args = parse_args()
    try:
        import wrds
    except ImportError as exc:
        raise SystemExit("The WRDS Python package is unavailable on this compute node") from exc

    args.output_dir.mkdir(parents=True, exist_ok=True)
    requested_kinds = ("trades", "quotes") if args.kind == "both" else (args.kind,)
    manifest = {
        "provider": "WRDS NYSE TAQ Daily Product",
        "schema": "taqmsec",
        "symbol": args.symbol,
        "start": args.start.isoformat(),
        "end_exclusive": args.end.isoformat(),
        "start_time": args.start_time,
        "end_time_exclusive": args.end_time,
        "format": args.format,
        "row_order": "time_m, time_m_nano and sequence number when available",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "wrds_user": getpass.getuser(),
        "files": [],
        "missing_tables": [],
    }

    tasks = [(day, kind) for day in dates(args.start, args.end) for kind in requested_kinds]
    started = time.monotonic()
    last_percent = progress(0, len(tasks), "starting", 0, started)
    total_rows = 0
    with wrds.Connection(wrds_username=getpass.getuser()) as db:
        for position, (day, kind) in enumerate(tasks, start=1):
            candidates = table_candidates(day, kind)
            present = available_tables(db, candidates)
            table = next((name for name in candidates if name in present), None)
            if table is None:
                manifest["missing_tables"].append({"date": day.isoformat(), "kind": kind,
                                                    "tried": list(candidates)})
            else:
                extension = ".parquet" if args.format == "parquet" else ".csv.gz"
                target = args.output_dir / f"{args.symbol}_{day.isoformat()}_{kind}{extension}"
                rows = export_table(db, table, args.symbol, args.start_time, args.end_time,
                                    target, args.chunk_size, args.format,
                                    args.compression_level)
                if rows:
                    manifest["files"].append({"file": target.name, "table": table, "rows": rows})
                    total_rows += rows
                else:
                    manifest["missing_tables"].append({"date": day.isoformat(), "kind": kind,
                                                        "table": table, "reason": "no matching rows"})
            last_percent = progress(position, len(tasks), f"{day} {kind}", total_rows,
                                    started, last_percent)

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {manifest_path}", flush=True)
    if not manifest["files"]:
        raise SystemExit("No TAQ rows were exported; inspect manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
