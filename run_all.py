#!/usr/bin/env python3
"""One command: isolated setup, data, backtest, reports, checks and optional app.

python run_all.py
    Run QQQ in WRDS M1 mode from C:\\data\\wrds_m1 and open the app.
python run_all.py --mode m1 --from 2018-01-01 --to 2019-01-01 --no-app
    Run an end-exclusive WRDS M1 period using only the M1 root.
python run_all.py --mode tick --no-app
    Use only raw WRDS files under C:\\data\\wrds_ticks, deriving signal bars therefrom.
python run_all.py --mode tick --from 2020-01-01 --to 2021-01-01 --no-app
    Run a bounded, end-exclusive raw-tick period.
python run_all.py --source dukascopy --data-only
    Download 2022-01-01 through the current UTC date unless dates are supplied.
python run_all.py --source csv --bars data/LOCAL_M1.csv --no-app
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DEFAULT_DUKASCOPY_START = "2022-01-01"
DEFAULT_WRDS_M1_ROOT = Path(os.environ.get("ORB_WRDS_M1_ROOT", r"C:\data\wrds_m1"))
DEFAULT_WRDS_TICK_ROOT = Path(os.environ.get("ORB_WRDS_TICK_ROOT", r"C:\data\wrds_ticks"))


class Progress:
    """Small dependency-free progress display for the end-to-end launcher."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self._write("starting")

    def _write(self, label: str) -> None:
        width = 30
        filled = round(width * self.done / self.total)
        bar = "#" * filled + "-" * (width - filled)
        percent = round(100 * self.done / self.total)
        print(f"[{bar}] {percent:3d}% {self.done}/{self.total} {label}", flush=True)

    def step(self, label: str) -> None:
        self.done += 1
        self._write(label)


class ReplayProgress:
    """At most one durable line per percentage point during long WRDS runs."""

    def __init__(self, label: str):
        self.label = label
        self.started = time.monotonic()
        self.last_percent = -1

    def __call__(self, done: int, total: int, detail: str) -> None:
        percent = 100 if total == 0 else int(done * 100 / total)
        if percent == self.last_percent and done != total:
            return
        self.last_percent = percent
        elapsed = time.monotonic() - self.started
        eta = elapsed * (total - done) / done if done else 0.0
        width = 30
        filled = width if total == 0 else int(width * done / total)
        bar = "#" * filled + "-" * (width - filled)
        eta_text = f" ETA {eta / 60:.1f}m" if done and done < total else ""
        print(f"[{bar}] {percent:3d}% {done}/{total} {self.label}: {detail} | "
              f"{elapsed / 60:.1f}m{eta_text}", flush=True)


def command(argv, **kwargs):
    print("+ " + subprocess.list2cmdline([str(x) for x in argv]), flush=True)
    return subprocess.run([str(x) for x in argv], cwd=ROOT, check=True, **kwargs)


def bootstrap(args) -> int | None:
    if args.no_install:
        return None
    env_dir = ROOT / ".venv"
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        command([sys.executable, "-m", "venv", env_dir])
    expected = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    marker = env_dir / ".orb-dependencies"
    if args.reinstall or not marker.exists() or marker.read_text() != expected:
        command([python, "-m", "pip", "install", "-e", ".[app,dev]"])
        marker.write_text(expected)
    return subprocess.call([str(python), str(ROOT / "run_all.py"), *sys.argv[1:], "--no-install"], cwd=ROOT)


def _tick_inventory(root: Path, manifests: list[Path]) -> list[tuple[Path, int]]:
    """Validate the manifest/file relationship and return declared row counts."""
    declared: dict[Path, int] = {}
    for manifest in manifests:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        files = metadata.get("files")
        if not isinstance(files, list):
            raise ValueError(f"WRDS tick manifest has no files list: {manifest}")
        for record in files:
            if not isinstance(record, dict) or not isinstance(record.get("file"), str):
                raise ValueError(f"WRDS tick manifest has an invalid file record: {manifest}")
            name = record["file"]
            if Path(name).name != name:
                raise ValueError(f"WRDS tick manifest file must be a basename: {manifest}: {name}")
            path = manifest.parent / name
            if path in declared:
                raise ValueError(f"WRDS tick file is declared more than once: {path}")
            try:
                rows = int(record["rows"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"WRDS tick manifest has no valid row count: {manifest}: {name}") from exc
            if rows <= 0:
                raise ValueError(f"WRDS tick manifest row count must be positive: {manifest}: {name}")
            declared[path] = rows

    actual = set(root.glob("*/*/*.parquet"))
    missing = sorted(path for path in declared if path not in actual or not path.is_file())
    unexpected = sorted(actual - set(declared))
    if missing:
        raise ValueError(f"WRDS tick files declared but absent: {missing[0]}")
    if unexpected:
        raise ValueError(f"WRDS tick files absent from manifests: {unexpected[0]}")
    if not declared:
        raise ValueError(f"No Parquet files are declared by WRDS tick manifests under {root}")
    return sorted(declared.items())


def _tick_source_signature(root: Path, args) -> str:
    """Fingerprint raw manifests and candle-building assumptions for a safe cache."""
    manifests = sorted(root.glob("*/*/manifest.json"))
    if not manifests:
        raise ValueError(f"No monthly WRDS tick manifests found under {root}")
    inventory = _tick_inventory(root, manifests)
    digest = hashlib.sha256()
    digest.update(str(root.resolve()).encode())
    for manifest in manifests:
        digest.update(str(manifest.relative_to(root)).encode())
        digest.update(manifest.read_bytes())
    for path, rows in inventory:
        stat = path.stat()
        digest.update(str(path.relative_to(root)).encode())
        digest.update(f"{rows}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    digest.update((ROOT / "scripts/wrds_build_m1_local.py").read_bytes())
    digest.update(json.dumps({
        "symbol": args.symbol.upper(), "start": args.start, "end": args.end,
        "start_time": "07:30:00", "end_time": "16:01:00",
        "point": 0.01, "strategy_offset_hours": 7,
    }, sort_keys=True).encode())
    return digest.hexdigest()[:20]


def _prepare_tick_signal_bars(args) -> tuple[Path, dict]:
    """Build/reuse M1 signal candles derived only from the selected raw tick root."""
    from scripts.wrds_build_m1_local import build

    root = args.tick_root.resolve()
    if not root.is_dir():
        raise ValueError(f"WRDS tick root does not exist: {root}")
    signature = _tick_source_signature(root, args)
    cache_parent = ROOT / ".cache" / "wrds_tick_m1"
    cache = cache_parent / signature
    manifest = cache / "manifest.json"
    if not manifest.exists():
        cache_parent.mkdir(parents=True, exist_ok=True)
        staging = cache_parent / f"{signature}.building-{uuid4().hex[:8]}"
        print("Tick mode: deriving M1/M15 signal candles from the raw tick tree (one-time cache)", flush=True)
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        build(root, staging, symbol=args.symbol.upper(), start=start, end=end,
              start_time="07:30:00", end_time="16:01:00", point=0.01,
              strategy_offset_hours=7)
        if cache.exists():
            raise ValueError(f"Incomplete tick-derived cache already exists: {cache}")
        staging.rename(cache)
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if metadata.get("source_root") != str(root):
        raise ValueError("Tick-derived signal cache points at a different raw root")
    years = cache / "years"
    if not years.is_dir() or not next(years.glob("*/*_M1.csv.gz"), None):
        raise ValueError(f"Tick-derived signal cache is incomplete: {cache}")
    return years, {
        "tick_root": str(root), "tick_manifest_count": len(list(root.glob("*/*/manifest.json"))),
        "derived_bar_cache": str(cache), "derived_bar_signature": signature,
        "derived_trading_days": metadata.get("trading_days"),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_artifact(path: Path) -> dict:
    """Pin one loaded input even when its WRDS sidecar was not transferred."""
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "name": resolved.name,
        "size_bytes": stat.st_size,
        "sha256": _sha256_file(resolved),
    }


def _slice_wrds_m1(m1, start: str | None, end: str | None):
    """Apply WRDS strategy-clock dates as a half-open interval."""
    import pandas as pd

    selected = m1
    if start:
        selected = selected[selected.index >= pd.Timestamp(start)]
    if end:
        selected = selected[selected.index < pd.Timestamp(end)]
    if selected.empty:
        bounds = f"[{start or '-infinity'}, {end or '+infinity'})"
        raise ValueError(f"No WRDS M1 bars in requested half-open date range {bounds}")
    return selected.copy()


def _wrds_prepared_signature(requested: Path, input_artifacts: list[dict], args,
                             tick_info: dict) -> str:
    """Fingerprint the exact inputs and selection behind a prepared M1 artifact."""
    payload = {
        "format_version": 1,
        "mode": args.mode,
        "source_root": str(requested.resolve()),
        "symbol": args.symbol.upper(),
        "start": args.start,
        "end_exclusive": args.end,
        "derived_bar_signature": tick_info.get("derived_bar_signature"),
        "inputs": input_artifacts,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]


def _materialize_wrds_m1(m1, requested: Path, input_artifacts: list[dict], args,
                         tick_info: dict) -> tuple[Path, str]:
    """Write or verify a mode- and source-specific prepared M1 cache."""
    import pandas as pd

    signature = _wrds_prepared_signature(requested, input_artifacts, args, tick_info)
    folder = ROOT / ".cache" / "wrds_prepared" / args.mode / signature
    start = args.start or m1.index.min().date().isoformat()
    end = args.end or (m1.index.max() + pd.Timedelta(days=1)).date().isoformat()
    target = folder / f"{args.symbol.upper()}_{start}_{end}_M1.csv.gz"
    manifest = folder / "manifest.json"
    if target.is_file() and manifest.is_file():
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
        if (metadata.get("source_signature") == signature
                and metadata.get("output_sha256") == _sha256_file(target)):
            return target, signature

    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / f".{target.name}.{uuid4().hex[:8]}.tmp"
    staging_manifest = folder / f".manifest.{uuid4().hex[:8]}.tmp"
    try:
        m1.to_csv(staging, compression={"method": "gzip", "mtime": 0}, float_format="%.8f")
        output_hash = _sha256_file(staging)
        staging_manifest.write_text(json.dumps({
            "source_signature": signature,
            "mode": args.mode,
            "source_root": str(requested.resolve()),
            "start": args.start,
            "end_exclusive": args.end,
            "rows": len(m1),
            "input_artifacts": input_artifacts,
            "output_file": target.name,
            "output_sha256": output_hash,
        }, indent=2) + "\n", encoding="utf-8")
        staging.replace(target)
        staging_manifest.replace(manifest)
    finally:
        staging.unlink(missing_ok=True)
        staging_manifest.unlink(missing_ok=True)
    return target, signature


def pipeline(args) -> tuple[Path, Path | None]:
    from orb import infer_minutes, load_bars, resample, run_backtest
    from orb.config import load_params
    from orb.reporting import file_hash, quality_report, save_result

    progress = Progress(3 if args.data_only else 4)
    params = load_params(args.config, args.set)
    params.validate(args.tf)
    progress.step("settings ready")
    source = {"provider": args.source}
    if args.source == "demo":
        from orb.synthetic import make_synthetic_m1
        data_path = ROOT / ".cache" / "demo" / "SYNTHETIC_NAS100_M1.csv"
        data_path.parent.mkdir(parents=True, exist_ok=True)
        m1 = make_synthetic_m1(days=args.demo_days, seed=args.seed)
        m1.to_csv(data_path, float_format="%.8f")
        source.update(label="Synthetic random-walk demo; no evidence of a trading edge",
                      seed=args.seed, days=args.demo_days, clock="simulated broker time",
                      synthetic=True)
    elif args.source == "dukascopy":
        from fetch_dukascopy import download
        if not args.start:
            raise ValueError("--source dukascopy requires --from YYYY-MM-DD")
        folder = ROOT / "data" / f"dukascopy_{args.symbol.upper()}_{args.start}_{args.end}_{args.tz}"
        source.update(download(args.symbol, args.start, args.end, folder, tz=args.tz,
                               scale=args.scale, point=params.point, spread=params.spread,
                               workers=args.workers, cache=ROOT / ".cache/dukascopy", ticks=args.ticks,
                               reuse=not args.refresh_data))
        data_path = folder / f"{args.symbol.upper()}_M1.csv"
        m1 = load_bars(data_path)
        source["label"] = f"Dukascopy {args.symbol.upper()} [{args.start}, {args.end}) UTC; clock {args.tz}"
    elif args.source == "wrds":
        import pandas as pd

        tick_info = {}
        if args.mode == "tick":
            requested, tick_info = _prepare_tick_signal_bars(args)
        else:
            if not args.bars:
                raise ValueError("M1 mode requires --bars FILE_OR_DIRECTORY")
            requested = args.bars.resolve()
        if requested.is_file():
            m1 = load_bars(requested)
            inputs = [requested]
        elif requested.is_dir():
            candidates = sorted({
                file for pattern in ("*_M1.csv", "*_M1.csv.gz")
                for file in requested.rglob(pattern)
                if "prepared" not in file.parts
            })
            if not candidates:
                raise ValueError(f"No *_M1.csv or *_M1.csv.gz files under {requested}")
            # A transferred gzip and an independently unpacked copy represent
            # the same input. Prefer gzip instead of treating that pair as two years.
            by_uncompressed_name = {}
            for file in candidates:
                key = Path(str(file)[:-3]) if file.suffix.lower() == ".gz" else file
                if key not in by_uncompressed_name or file.suffix.lower() == ".gz":
                    by_uncompressed_name[key] = file
            inputs = sorted(by_uncompressed_name.values())
            frames = [load_bars(file) for file in inputs]
            m1 = pd.concat(frames).sort_index()
            if m1.index.has_duplicates:
                duplicate = m1.index[m1.index.duplicated()].unique()[0]
                raise ValueError(f"WRDS inputs overlap at {duplicate}; keep one file per date range")
        else:
            raise ValueError(f"WRDS bars path does not exist: {requested}")
        manifest_files = []
        missing_manifests = []
        input_artifacts = []
        for file in inputs:
            artifact = _input_artifact(file)
            input_artifacts.append(artifact)
            manifest_path = file.parent / "manifest.json"
            if not manifest_path.exists():
                missing_manifests.append(str(manifest_path))
                continue
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if metadata.get("symbol") != args.symbol.upper():
                raise ValueError(f"WRDS manifest symbol mismatch: {manifest_path}")
            if metadata.get("file") and metadata["file"] != file.name:
                raise ValueError(f"WRDS manifest file mismatch: {manifest_path}")
            if metadata.get("sha256") and metadata["sha256"] != artifact["sha256"]:
                raise ValueError(f"WRDS input checksum mismatch: {file}")
            if metadata.get("strategy_clock") != "New York wall time + 7 hours":
                raise ValueError(f"WRDS manifest strategy clock is not ny+7: {manifest_path}")
            if metadata.get("point") is not None and float(metadata["point"]) != params.point:
                raise ValueError(f"WRDS manifest point does not match the config: {manifest_path}")
            manifest_files.append(str(manifest_path))
        if manifest_files and missing_manifests:
            raise ValueError("Some WRDS inputs have manifests and others do not; keep a complete provenance set")
        provenance = "validated" if manifest_files else "manifests not supplied"
        if missing_manifests:
            print("WRDS provenance warning: no sibling manifest.json; download the year manifest too", flush=True)
        m1 = _slice_wrds_m1(m1, args.start, args.end)
        if requested.is_dir() or args.start or args.end:
            data_path, prepared_signature = _materialize_wrds_m1(
                m1, requested, input_artifacts, args, tick_info,
            )
        else:
            data_path, prepared_signature = requested, None
        source.update(
            label=(f"WRDS NYSE TAQ / {args.symbol.upper()} event-level NBBO execution "
                   "with signal bars derived from the same raw tick root"
                   if args.mode == "tick" else
                   f"WRDS NYSE TAQ / {args.symbol.upper()} consolidated NBBO M1"),
            mode=args.mode,
            clock="New York wall time + 7 hours",
            price_basis=("signals from valid NBBO best-bid M1; executions from valid event-level NBBO bid/ask"
                         if args.mode == "tick" else
                         "M1 OHLC from valid NBBO best bid; spread is the within-minute p95"),
            input_files=[file.name for file in inputs],
            input_artifacts=input_artifacts,
            manifest_validation=provenance,
            manifest_files=manifest_files,
            requested_start=args.start,
            requested_end_exclusive=args.end,
            selected_first=str(m1.index[0]),
            selected_last=str(m1.index[-1]),
            prepared_signature=prepared_signature,
            **tick_info,
        )
    else:
        if not args.bars:
            raise ValueError("--source csv requires --bars PATH to M1 bars in strategy clock time")
        data_path = args.bars.resolve()
        m1 = load_bars(data_path)
        source.update(label=f"Local CSV: {data_path.name}", clock="user-provided strategy clock")
    progress.step(f"data ready ({len(m1):,} M1 bars)")
    if infer_minutes(m1) != 1:
        raise ValueError("run_all requires M1 bars. Use the app or engine API for coarser bars.")
    if params.use_data_spread and not (m1["spread"] > 0).any():
        raise ValueError("use_data_spread=true, but the input has no positive recorded spreads")
    quality = quality_report(m1, args.tf, params.orb_hour, params.orb_minute)
    incomplete = quality["incomplete_or_missing_opening_ranges"]
    if incomplete:
        rejected_dates = {pd.Timestamp(value) for value in incomplete}
        before = len(m1)
        m1 = m1[~m1.index.normalize().isin(rejected_dates)].copy()
        quality["excluded_incomplete_opening_range_dates"] = incomplete
        quality["rows_excluded_with_incomplete_opening_ranges"] = before - len(m1)
        quality["rows_after_exclusions"] = len(m1)
        quality["note"] = (
            "Gaps include scheduled closures. No prices are forward-filled. "
            "Dates with incomplete opening-range candles are excluded and listed."
        )
        print("Excluded incomplete opening-range dates: " + ", ".join(incomplete), flush=True)
        if m1.empty:
            raise ValueError("No rows remain after excluding incomplete opening-range dates")
    progress.step("data validated")
    if args.data_only:
        print(f"Data ready: {data_path.resolve()} ({len(m1):,} M1 bars)")
        return data_path, None
    bars = resample(m1, args.tf)
    execution = None
    if args.mode == "tick":
        from orb import NbboExecution, NbboExecutionConfig, TickStore

        store = TickStore(args.tick_root, strategy_offset_hours=7,
                          symbol=args.symbol, max_cache_days=args.tick_cache_days)
        execution_config = NbboExecutionConfig(
            latency_ms=args.tick_latency_ms,
            max_entry_delay_ms=args.tick_max_entry_delay_ms,
            max_eod_delay_ms=args.tick_max_eod_delay_ms,
            strict=True,
        )
        execution = NbboExecution(store, execution_config)
        source.update(
            execution_model="nbbo_top_of_book", execution_strict=True,
            tick_latency_ms=args.tick_latency_ms,
            tick_max_entry_delay_ms=args.tick_max_entry_delay_ms,
            tick_max_eod_delay_ms=args.tick_max_eod_delay_ms,
        )
    replay_progress = ReplayProgress("tick replay" if execution is not None else "M1 replay") \
        if args.mode in ("m1", "tick") else None
    result = run_backtest(bars, params, path=m1, execution=execution, progress=replay_progress)
    if result.days.empty:
        raise ValueError("No opening ranges found. Check timing settings and data clock.")
    if execution is not None:
        coverage = list(store.coverage_log.values())
        source["tick_files_used"] = sorted({str(item.source_path) for item in coverage})
        quality["tick_execution"] = {
            "source_days_loaded": len(coverage),
            "source_rows": sum(item.source_rows for item in coverage),
            "valid_rows": sum(item.rows for item in coverage),
            "dropped_timestamps": sum(item.dropped_timestamps for item in coverage),
            "dropped_invalid": sum(item.dropped_invalid for item in coverage),
            "dropped_crossed": sum(item.dropped_crossed for item in coverage),
            "duplicate_timestamps": sum(item.duplicate_timestamps for item in coverage),
        }
    source.update(file=data_path.name, sha256=file_hash(data_path), signal_minutes=args.tf)
    output = args.output or ROOT / "outputs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    report = save_result(result, output, source, quality)
    progress.step("backtest and report complete")
    print(f"Trades: {result.stats['trades']} | gross R: {result.stats['total_r']:.2f} | "
          f"closed-balance return: {result.stats['return_pct']:.2f}%")
    print(f"Report: {report.resolve()}")
    return data_path, report.parent


def _is_path_within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["m1", "tick"],
                    help="WRDS mode: M1 uses only the M1 root; tick uses only the raw tick root")
    ap.add_argument("--source", choices=["auto", "demo", "dukascopy", "wrds", "csv"], default="wrds",
                    help="Data provider; default is WRDS QQQ M1 (demo and Dukascopy are explicit)")
    ap.add_argument("--from", dest="start",
                    help=f"Start date (WRDS strategy clock; Dukascopy UTC default {DEFAULT_DUKASCOPY_START})")
    ap.add_argument("--to", dest="end",
                    help="End-exclusive date; WRDS defaults to all coverage, Dukascopy to current UTC")
    ap.add_argument("--symbol", default="USATECHIDXUSD")
    ap.add_argument("--tz", default="ny+7", help="UTC input -> strategy clock: ny+7, utc, +3")
    ap.add_argument("--scale", type=float)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ticks", action="store_true", help="Also archive ticks; engine still uses M1")
    ap.add_argument("--refresh-data", action="store_true",
                    help="Rebuild an exact Dukascopy dataset instead of reusing its verified local files")
    ap.add_argument("--data-only", action="store_true",
                    help="Install dependencies, prepare and validate data, then stop")
    ap.add_argument("--bars", type=Path)
    ap.add_argument("--tick-root", type=Path,
                    help=f"Raw WRDS tick tree; tick mode default: {DEFAULT_WRDS_TICK_ROOT}")
    ap.add_argument("--tick-latency-ms", type=float, default=0.0,
                    help="Assumed delay from order activation to NBBO eligibility")
    ap.add_argument("--tick-max-entry-delay-ms", type=float, default=1000.0,
                    help="Fail if the first valid entry quote arrives later")
    ap.add_argument("--tick-max-eod-delay-ms", type=float, default=1000.0,
                    help="Fail if the first valid EOD quote arrives later")
    ap.add_argument("--tick-cache-days", type=int, default=2,
                    help="Maximum raw quote days retained in memory")
    ap.add_argument("--config", type=Path,
                    help="Settings JSON; defaults to configs/qqq.json for WRDS and baseline.json otherwise")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--tf", type=int, default=15, choices=[5, 15, 30, 60])
    ap.add_argument("--demo-days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", type=Path, help="New result directory; existing directories are refused")
    ap.add_argument("--check", action="store_true", help="Run lint and tests before the pipeline")
    ap.add_argument("--compile-mt5", action="store_true", help="Compile EA with installed MetaEditor (Windows)")
    ap.add_argument("--no-app", action="store_true", help="Finish after writing reports")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--no-install", action="store_true", help="Use current Python; no package changes")
    ap.add_argument("--reinstall", action="store_true")
    ap.add_argument("--ea-log", type=Path, help="Reconcile local CSV against an EA log; requires --source csv")
    args = ap.parse_args(argv)
    if args.mode:
        if args.source not in ("auto", "wrds"):
            ap.error("--mode can only be used with WRDS data")
        args.source = "wrds"
    if args.source == "auto":
        if args.bars:
            args.source = "csv"
        else:
            args.source = "wrds"
    if args.source == "wrds":
        args.mode = args.mode or "m1"
        if args.mode == "m1":
            if args.tick_root is not None:
                ap.error("M1 mode does not accept --tick-root")
            args.bars = args.bars or DEFAULT_WRDS_M1_ROOT
            if _is_path_within(args.bars, DEFAULT_WRDS_TICK_ROOT):
                ap.error("M1 mode --bars cannot point at the WRDS tick root")
        else:
            if args.bars is not None:
                ap.error("Tick mode does not accept --bars; it derives candles from the tick root")
            args.tick_root = args.tick_root or DEFAULT_WRDS_TICK_ROOT
            if _is_path_within(args.tick_root, DEFAULT_WRDS_M1_ROOT):
                ap.error("Tick mode --tick-root cannot point at the WRDS M1 root")
    elif args.tick_root is not None:
        ap.error("--tick-root requires --mode tick")
    if args.demo_days < 1:
        ap.error("--demo-days must be positive")
    if not 1 <= args.port <= 65535:
        ap.error("--port must be 1-65535")
    if args.ea_log and args.source != "csv":
        ap.error("--ea-log requires --source csv with data from the same MT5 run")
    if args.ea_log and args.data_only:
        ap.error("--ea-log cannot be combined with --data-only")
    if args.ticks and args.source != "dukascopy":
        ap.error("--ticks requires --source dukascopy")
    if args.refresh_data and args.source != "dukascopy":
        ap.error("--refresh-data requires --source dukascopy")
    if args.source == "dukascopy":
        args.start = args.start or DEFAULT_DUKASCOPY_START
        args.end = args.end or datetime.now(timezone.utc).date().isoformat()
    for flag, value in (("--from", args.start), ("--to", args.end)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                ap.error(f"{flag} must be an ISO date (YYYY-MM-DD)")
    if args.start and args.end and args.end <= args.start:
        ap.error("--to must be later than --from")
    if args.source == "wrds" and args.symbol == "USATECHIDXUSD":
        args.symbol = "QQQ"
    if args.mode == "tick":
        for name in ("tick_latency_ms", "tick_max_entry_delay_ms", "tick_max_eod_delay_ms"):
            value = getattr(args, name)
            if not (value >= 0 and value < float("inf")):
                ap.error(f"--{name.replace('_', '-')} must be finite and nonnegative")
        if args.tick_cache_days < 0:
            ap.error("--tick-cache-days must be nonnegative")
    if args.config is None:
        args.config = ROOT / "configs" / ("qqq.json" if args.source == "wrds" else "baseline.json")
    return args


def app_environment(args, data_path: Path, output: Path) -> dict[str, str]:
    """Build an explicit, mode-isolated environment for the Streamlit process."""
    env = os.environ.copy()
    tick_only = (
        "ORB_TICK_ROOT", "ORB_WRDS_TICK_ROOT", "ORB_TICK_LATENCY_MS",
        "ORB_TICK_MAX_ENTRY_DELAY_MS", "ORB_TICK_MAX_EOD_DELAY_MS", "ORB_TICK_CACHE_DAYS",
    )
    for name in ("ORB_WRDS_M1_ROOT", "ORB_SOURCE_ROOT", *tick_only):
        env.pop(name, None)
    env["ORB_DATA_FILE"] = str(data_path.resolve())
    env["ORB_PARAMS_FILE"] = str((output / "params.json").resolve())
    env["ORB_SIGNAL_TF"] = str(args.tf)
    env["ORB_BACKTEST_MODE"] = args.mode or "m1"
    if args.mode == "tick":
        root = str(args.tick_root.resolve())
        env.update({
            "ORB_SOURCE_ROOT": root,
            "ORB_TICK_ROOT": root,
            "ORB_WRDS_TICK_ROOT": root,
            "ORB_EXECUTION_MODEL": "nbbo_top_of_book",
            "ORB_EXECUTION_TIMING": "event_level",
            "ORB_TICK_LATENCY_MS": str(args.tick_latency_ms),
            "ORB_TICK_MAX_ENTRY_DELAY_MS": str(args.tick_max_entry_delay_ms),
            "ORB_TICK_MAX_EOD_DELAY_MS": str(args.tick_max_eod_delay_ms),
            "ORB_TICK_CACHE_DAYS": str(args.tick_cache_days),
        })
    else:
        source = args.bars.resolve() if args.bars is not None else data_path.resolve()
        env.update({
            "ORB_SOURCE_ROOT": str(source),
            "ORB_EXECUTION_MODEL": "m1_bar_path",
            "ORB_EXECUTION_TIMING": "bar_ohlc",
        })
        if args.mode == "m1" and args.source == "wrds":
            env["ORB_WRDS_M1_ROOT"] = str(source)
    return env


def main() -> int:
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        print("Python 3.11, 3.12 or 3.13 is required.", file=sys.stderr)
        return 1
    args = parse_args()
    try:
        status = bootstrap(args)
        if status is not None:
            return status
        if args.compile_mt5:
            if os.name != "nt":
                raise ValueError("--compile-mt5 requires Windows with MetaTrader 5 installed")
            command(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts/compile_mt5.ps1"])
        if args.check:
            command([sys.executable, "-m", "ruff", "check", "."])
            command([sys.executable, "-m", "pytest", "-q"])
        data_path, output = pipeline(args)
        if output is None:
            return 0
        if args.ea_log:
            from orb.config import load_params
            p = load_params(output / "params.json")
            command([sys.executable, "reconcile.py", "--ea-log", args.ea_log,
                     "--bars", data_path, "--m1", data_path, "--report", output / "reconcile.csv",
                     "--strict", "--spread", str(p.spread), "--point", str(p.point),
                     *(["--use-data-spread"] if p.use_data_spread else [])])
        if not args.no_app:
            env = app_environment(args, data_path, output)
            print(f"App: http://localhost:{args.port} (Ctrl+C to stop)", flush=True)
            command([sys.executable, "-m", "streamlit", "run", "app.py",
                     "--server.address=localhost", f"--server.port={args.port}"], env=env)
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as exc:
        print(f"ORB failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
