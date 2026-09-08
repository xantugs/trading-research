#!/usr/bin/env python3
"""One command: isolated setup, data, backtest, reports, optional checks and app.

python run_all.py
python run_all.py --source dukascopy --from 2025-01-01 --to 2025-02-01 --no-app
python run_all.py --source csv --bars data/NAS100_M1.csv --no-app
python run_all.py --check --no-app
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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


def pipeline(args) -> tuple[Path, Path]:
    from orb import infer_minutes, load_bars, resample, run_backtest
    from orb.config import load_params
    from orb.reporting import file_hash, quality_report, save_result

    params = load_params(args.config, args.set)
    params.validate(args.tf)
    source = {"provider": args.source}
    if args.source == "demo":
        from orb.synthetic import make_synthetic_m1
        data_path = ROOT / "data/SYNTHETIC_NAS100_M1.csv"
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
                               workers=args.workers, cache=ROOT / ".cache/dukascopy", ticks=args.ticks))
        data_path = folder / f"{args.symbol.upper()}_M1.csv"
        m1 = load_bars(data_path)
        source["label"] = f"Dukascopy {args.symbol.upper()} [{args.start}, {args.end}) UTC; clock {args.tz}"
    else:
        if not args.bars:
            raise ValueError("--source csv requires --bars PATH to M1 bars in strategy clock time")
        data_path = args.bars.resolve()
        m1 = load_bars(data_path)
        source.update(label=f"Local CSV: {data_path.name}", clock="user-provided strategy clock")
    if infer_minutes(m1) != 1:
        raise ValueError("run_all requires M1 bars. Use the app or engine API for coarser bars.")
    quality = quality_report(m1, args.tf, params.orb_hour, params.orb_minute)
    if quality["incomplete_or_missing_opening_ranges"]:
        raise ValueError("Incomplete/missing opening range on observed days: " +
                         ", ".join(quality["incomplete_or_missing_opening_ranges"][:10]) +
                         ". Check coverage and timezone; use a complete session subset.")
    bars = resample(m1, args.tf)
    result = run_backtest(bars, params, path=m1)
    if result.days.empty:
        raise ValueError("No opening ranges found. Check timing settings and data clock.")
    source.update(file=data_path.name, sha256=file_hash(data_path), signal_minutes=args.tf)
    output = args.output or ROOT / "outputs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    report = save_result(result, output, source, quality)
    print(f"Trades: {result.stats['trades']} | gross R: {result.stats['total_r']:.2f} | "
          f"closed-balance return: {result.stats['return_pct']:.2f}%")
    print(f"Report: {report.resolve()}")
    return data_path, report.parent


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["demo", "dukascopy", "csv"], default="demo")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--symbol", default="USATECHIDXUSD")
    ap.add_argument("--tz", default="ny+7", help="UTC input -> strategy clock: ny+7, utc, +3")
    ap.add_argument("--scale", type=float)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--ticks", action="store_true", help="Also archive ticks; engine still uses M1")
    ap.add_argument("--bars", type=Path)
    ap.add_argument("--config", type=Path, default=ROOT / "configs/baseline.json")
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
    if args.demo_days < 1:
        ap.error("--demo-days must be positive")
    if not 1 <= args.port <= 65535:
        ap.error("--port must be 1-65535")
    if args.ea_log and args.source != "csv":
        ap.error("--ea-log requires --source csv with data from the same MT5 run")
    if args.ticks and args.source != "dukascopy":
        ap.error("--ticks requires --source dukascopy")
    return args


def main() -> int:
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
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
        if args.ea_log:
            from orb.config import load_params
            p = load_params(output / "params.json")
            command([sys.executable, "reconcile.py", "--ea-log", args.ea_log,
                     "--bars", data_path, "--m1", data_path, "--report", output / "reconcile.csv",
                     "--strict", "--spread", str(p.spread), "--point", str(p.point),
                     *(["--use-data-spread"] if p.use_data_spread else [])])
        if not args.no_app:
            env = os.environ.copy()
            env["ORB_DATA_FILE"] = str(data_path.resolve())
            env["ORB_PARAMS_FILE"] = str((output / "params.json").resolve())
            env["ORB_SIGNAL_TF"] = str(args.tf)
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
