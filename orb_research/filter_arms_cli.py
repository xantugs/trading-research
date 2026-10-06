"""Run the prespecified entry-rule / exit arms through the frozen 2018–2024 walk-forward."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import load_config
from .data_loader import load_m1
from .filter_arms import prespecified_arms, run_arms, write_results
from .walk_forward import run_walk_forward, validate_frozen_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "walk_forward_base.json"


def _require_cached_months(config) -> None:
    cache = Path(config.cache_dir) / config.expected_server / config.symbol
    month = datetime.fromisoformat(config.start)
    end = datetime.fromisoformat(config.development_end)
    while month < end:
        path = cache / f"{month:%Y%m}.parquet"
        if not path.is_file():
            raise ValueError(f"cached development month missing; no download attempted: {path}")
        month = (month.replace(year=month.year + 1, month=1) if month.month == 12
                 else month.replace(month=month.month + 1))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--arms", nargs="*", help="subset of arm keys to run (default: all)")
    parser.add_argument("--skip-baseline-check", action="store_true",
                        help="do not rerun walk_forward.run_walk_forward to confirm the baseline arm")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    validate_frozen_config(config)  # before any market data is read
    _require_cached_months(config)
    arms = prespecified_arms()
    if args.arms:
        unknown = sorted(set(args.arms) - {arm.key for arm in arms})
        if unknown:
            raise ValueError(f"unknown arm keys: {', '.join(unknown)}")
        arms = tuple(arm for arm in arms if arm.key in set(args.arms) or arm.key == "base_1600_fixed")

    print("Loading cached FTMO development M1 bars through 2024 only...", flush=True)
    m1, provenance = load_m1(config)
    print(f"Loaded {len(m1):,} M1 bars; latest {m1.index[-1]}; broker_clock={config.broker_clock}",
          flush=True)
    results = run_arms(m1, config, arms, emit=lambda message: print(message, flush=True))

    baseline_check = None
    if not args.skip_baseline_check:
        print("Re-running walk_forward.run_walk_forward for the baseline check...", flush=True)
        reference = run_walk_forward(m1, config, emit=lambda message: None)
        arm = results["arms"]["base_1600_fixed"]["combined"]
        ref = reference["combined"]
        same = (arm["trades"] == ref["trades"] and arm["mean_r"] is not None and ref["mean_r"] is not None
                and abs(arm["mean_r"] - ref["mean_r"]) < 1e-12)
        baseline_check = {"status": "identical" if same else "DIFFERENT",
                          "arm_trades": arm["trades"], "walk_forward_trades": ref["trades"],
                          "arm_mean_r": arm["mean_r"], "walk_forward_mean_r": ref["mean_r"]}
        print(f"Baseline check: {baseline_check['status']}", flush=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_dir or ROOT / "outputs" / "filter_arms" / f"{stamp}-{uuid4().hex[:6]}"
    if not output.is_absolute():
        output = ROOT / output
    report = write_results(output, results, config, provenance, baseline_check)
    print(json.dumps({"report": str(report),
                      "arms": {key: item["combined"] for key, item in results["arms"].items()},
                      "holdout_used": False}, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
