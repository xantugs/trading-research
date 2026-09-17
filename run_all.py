#!/usr/bin/env python3
"""One-command FTMO XAUUSD data preparation and development backtest."""
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
DEFAULT_CONFIG = Path("configs/xauusd_orb_research.json")


def _research_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def command(argv: list[object]) -> None:
    rendered = [str(item) for item in argv]
    print("+ " + subprocess.list2cmdline(rendered), flush=True)
    subprocess.run(rendered, cwd=ROOT, check=True)


def ensure_research_python() -> Path:
    """Install only the dependencies needed for this FTMO research project."""
    python = _research_python()
    if not python.exists():
        command([sys.executable, "-m", "venv", ROOT / ".venv"])
    marker = ROOT / ".venv" / ".orb-research-dependencies"
    expected = hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()
    if not marker.exists() or marker.read_text(encoding="utf-8") != expected:
        command([python, "-m", "pip", "install", "-e", ".[research-v2]"])
        marker.write_text(expected, encoding="utf-8")
    return python


def _config_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def run_broker_probe(argv: list[str]) -> int:
    if os.name != "nt":
        raise ValueError("MT5 broker probing requires Windows")
    python = ensure_research_python()
    command([python, "-m", "scripts.probe_mt5_history", *argv])
    return 0


def run_web(argv: list[str]) -> int:
    """Start the private dashboard with the same local Python environment."""
    python = ensure_research_python()
    command([python, "-m", "webapp.server", *argv])
    return 0


def run_ftmo_data(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python run_all.py ftmo-data",
        description="Download or reuse FTMO XAUUSD history and verify every requested partition.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat(),
                        help="Exclusive UTC date; defaults to today")
    parser.add_argument("--terminal-path", help="FTMO terminal64.exe if multiple terminals are installed")
    parser.add_argument("--with-ticks", action="store_true", help="Also archive native bid/ask ticks")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch previously cached partitions")
    parser.add_argument("--m1-quality-report", type=Path, default=Path("outputs/ftmo_m1_quality.json"))
    parser.add_argument("--tick-quality-report", type=Path, default=Path("outputs/ftmo_tick_quality.json"))
    args = parser.parse_args(argv)
    date.fromisoformat(args.end)
    python = ensure_research_python()
    selected = _config_path(args.config)
    base = ["--config", selected, "--end", args.end]
    if args.terminal_path:
        base.extend(["--terminal-path", args.terminal_path])
    if args.refresh:
        base.append("--refresh")
    command([python, "-m", "scripts.download_mt5_m1", *base])
    command([python, "-m", "scripts.verify_mt5_m1_cache", "--config", selected,
             "--report", args.m1_quality_report])
    if args.with_ticks:
        command([python, "-m", "scripts.download_mt5_ticks", *base])
        command([python, "-m", "scripts.verify_mt5_ticks_cache", "--config", selected,
                 "--report", args.tick_quality_report])
    return 0


def _prepare_backtest_config(source: Path, output: Path) -> Path:
    """Lock user-selected parameters into a new, reviewable run directory."""
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("backtest configuration must be a JSON object")
    if raw.get("source", "mt5") != "mt5":
        raise ValueError("the FTMO backtest command requires the MT5 cache source")
    end = date.fromisoformat(raw.get("development_end", "2025-01-01"))
    holdout = date.fromisoformat(raw.get("holdout_start", "2025-01-01"))
    if end > holdout:
        raise ValueError("development_end must not pass the sealed holdout_start")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"run directory already contains files: {output}")
    output.mkdir(parents=True, exist_ok=True)
    raw["output_dir"] = str(output.resolve())
    effective = output / "parameters.json"
    effective.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return effective


def _require_cached_development(config: dict) -> None:
    """Prevent cache-only mode from silently requesting a missing MT5 month."""
    start = date.fromisoformat(config.get("start", "2018-01-01"))
    end = date.fromisoformat(config.get("development_end", "2025-01-01"))
    month = start.replace(day=1)
    root = (Path(config.get("cache_dir", "C:/data/ftmo_xauusd_m1"))
            / config.get("expected_server", "FTMO-Demo") / config.get("symbol", "XAUUSD"))
    while month < end:
        target = root / f"{month:%Y%m}.parquet"
        if not target.is_file():
            raise ValueError(f"cached-only run needs M1 month {month:%Y-%m}: {target}")
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)


class Progress:
    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self._write("starting")

    def _write(self, label: str) -> None:
        filled = round(30 * self.done / self.total)
        print(f"[{'#' * filled}{'-' * (30 - filled)}] "
              f"{round(100 * self.done / self.total):3d}% "
              f"{self.done}/{self.total} {label}", flush=True)

    def step(self, label: str) -> None:
        self.done += 1
        self._write(label)


def run_backtest(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python run_all.py backtest",
        description="Prepare FTMO data and run a complete development-period backtest.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="Strategy and cost parameters (JSON)")
    parser.add_argument("--output-dir", type=Path, help="Fresh run directory; defaults to a timestamped path")
    parser.add_argument("--terminal-path", help="FTMO terminal64.exe when several MT5 terminals are open")
    parser.add_argument("--cached-only", action="store_true",
                        help="Verify and reuse downloaded data without connecting to MT5")
    parser.add_argument("--skip-ticks", action="store_true",
                        help="Run the M1 baseline without downloading or checking tick data")
    parser.add_argument("--post-baseline-analysis", action="store_true",
                        help="Also run optional Monte Carlo, cost and illustrative prop scenarios")
    parser.add_argument("--prop-rules", type=Path, default=Path("configs/prop_research_example.json"),
                        help="Illustrative prop-rule JSON used only with --post-baseline-analysis")
    args = parser.parse_args(argv)
    started = time.monotonic()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_dir or ROOT / "outputs" / "runs" / f"{stamp}-{uuid4().hex[:6]}"
    if not output.is_absolute():
        output = ROOT / output
    effective = _prepare_backtest_config(_config_path(args.config), output)
    selected = json.loads(effective.read_text(encoding="utf-8"))
    end = selected.get("development_end", "2025-01-01")
    progress = Progress(5)
    print(f"Parameters: {effective}", flush=True)
    print(f"Development ends before {end}; later data is excluded from this backtest.", flush=True)

    if args.cached_only:
        python = ensure_research_python()
        _require_cached_development(selected)
        command([python, "-m", "scripts.verify_mt5_m1_cache", "--config", effective,
                 "--report", output / "m1_quality.json"])
        if not args.skip_ticks:
            command([python, "-m", "scripts.verify_mt5_ticks_cache", "--config", effective,
                     "--report", output / "tick_quality.json"])
    else:
        data_args = ["--config", str(effective), "--end", end,
                     "--m1-quality-report", str(output / "m1_quality.json"),
                     "--tick-quality-report", str(output / "tick_quality.json")]
        if not args.skip_ticks:
            data_args.append("--with-ticks")
        if args.terminal_path:
            data_args.extend(["--terminal-path", args.terminal_path])
        run_ftmo_data(data_args)
        python = _research_python()
    progress.step("broker data prepared and verified")
    main_command = [python, ROOT / "main.py", "--config", effective]
    if args.post_baseline_analysis:
        main_command.extend(["--post-baseline-analysis", "--prop-rules", _config_path(args.prop_rules)])
    command(main_command)
    progress.step("backtest and charts complete")
    command([python, "-m", "scripts.audit_ftmo_baseline", "--config", effective])
    progress.step("representative trades audited")
    if args.skip_ticks:
        progress.step("tick comparison skipped by request")
    else:
        command([python, "-m", "scripts.compare_ftmo_tick_exits", "--config", effective])
        progress.step("tick order compared")

    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))
    m1_quality = json.loads((output / "m1_quality.json").read_text(encoding="utf-8"))
    tick_quality = (json.loads((output / "tick_quality.json").read_text(encoding="utf-8"))
                    if not args.skip_ticks else None)
    tick_sample = (json.loads((output / "sample_tick_exit_comparison.json").read_text(encoding="utf-8"))
                   if not args.skip_ticks else [])
    disagreements = sum(row.get("agrees") is False for row in tick_sample)
    audit_rows = json.loads((output / "trade_audit.json").read_text(encoding="utf-8"))
    status = {
        "parameters": str(effective.resolve()), "development_end_exclusive": end,
        "holdout_used": False, "trades": metrics["trades"],
        "mean_net_r": metrics["expectancy_r"], "net_return_pct": metrics["net_return_pct"],
        "signals_skipped": diagnostics["skipped"],
        "m1_zero_spread_bars": m1_quality["zero_spread_bars"],
        "tick_history_days_missing": (tick_quality["m1_days_without_ticks"] if tick_quality else None),
        "session_minutes_without_ticks": (tick_quality["session_minutes_without_ticks"]
                                          if tick_quality else None),
        "sampled_tick_exit_disagreements": disagreements if tick_quality else None,
        "sampled_bar_audits_passed": len(audit_rows),
        "reports": [path.name for path in sorted(output.iterdir()) if path.is_file()]
                   + ["workflow_status.json"],
    }
    (output / "workflow_status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    progress.step("result bundle complete")
    print(f"Run folder: {output.resolve()}", flush=True)
    print(f"Trades: {metrics['trades']:,} | mean net R: {metrics['expectancy_r']:+.3f} | "
          f"return: {metrics['net_return_pct']:+.2f}%", flush=True)
    print(f"Skipped signals: {diagnostics['skipped']} | "
          f"zero-spread M1 bars: {m1_quality['zero_spread_bars']:,}", flush=True)
    if tick_quality:
        print(f"Tick gaps: {len(tick_quality['m1_days_without_ticks'])} full trading day(s), "
              f"{tick_quality['session_minutes_without_ticks']:,} session minute(s); "
              f"selected exit-order differences: {disagreements}", flush=True)
    print(f"Elapsed: {(time.monotonic() - started) / 60:.1f} minutes", flush=True)
    return 0


def main() -> int:
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        print("Python 3.11, 3.12 or 3.13 is required.", file=sys.stderr)
        return 1
    commands = {"backtest": run_backtest, "ftmo-data": run_ftmo_data,
                "broker-probe": run_broker_probe, "web": run_web}
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("FTMO XAUUSD ORB research\n\n"
              "  python run_all.py backtest --config configs/xauusd_orb_research.json\n"
              "  python run_all.py backtest --cached-only\n"
              "  python run_all.py ftmo-data --with-ticks\n"
              "  python run_all.py broker-probe --symbol XAUUSD\n"
              "  python run_all.py web --port 8520\n"
              "  python run_all.py web --tunnel\n")
        return 0
    selected = sys.argv[1]
    if selected not in commands:
        print(f"Unknown command {selected!r}. Run python run_all.py --help.", file=sys.stderr)
        return 2
    try:
        return commands[selected](sys.argv[2:])
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError) as exc:
        print(f"FTMO workflow stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
