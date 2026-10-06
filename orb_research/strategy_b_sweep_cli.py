"""Run the single prespecified Strategy B 4×4 training-only extension."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .config import load_config
from .data_loader import decode_broker_clock, load_m1, validate_m1
from .strategy_b_sweep import run_sweep, write_results
from .walk_forward import validate_frozen_config
from scripts.verify_broker_clock import verify


def _require_sizing_audit(path: Path) -> None:
    spec = json.loads((path / "sizing_spec.json").read_text(encoding="utf-8"))
    if spec["research_initial_equity"] != 100_000 or spec["holdout_used"]:
        raise ValueError("a sealed $100k research-sizing run is required before the sweep")
    folds = pd.read_csv(path / "arms_folds.csv")
    baseline = folds.loc[folds.arm == "base_1600_fixed"]
    if len(baseline) != 4:
        raise ValueError("research-sizing baseline must contain all four folds")
    skipped = [json.loads(value).get("below_min_lot", 0) for value in baseline.test_skipped]
    if any(skipped):
        raise ValueError(f"research sizing still censors minimum lots by fold: {skipped}; stop sweep")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/strategy_b_0820.json"))
    parser.add_argument("--research-sizing-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    _require_sizing_audit(args.research_sizing_dir)
    base = load_config(args.config)
    validate_frozen_config(base)
    if base.broker_clock != "new_york_plus_7" or base.initial_equity != 5000:
        raise ValueError("sweep starts from the frozen $5k Strategy B config")
    raw, provenance = load_m1(replace(base, broker_clock="stored_as_utc"))
    clock = verify(raw, base)
    if not clock["pass"]:
        raise ValueError("broker clock failed verification; no strategy outcome computed")
    research = replace(base, initial_equity=100_000.0)
    m1 = validate_m1(decode_broker_clock(raw, research), research)
    result = run_sweep(m1, research, emit=lambda message: print(message, flush=True))
    report = write_results(args.output_dir, result, research, provenance)
    (args.output_dir / "clock_verification.json").write_text(
        json.dumps(clock, indent=2) + "\n", encoding="utf-8")
    path = result["selected_path"]
    print(json.dumps({"clock": "PASS", "selected_path": path[["test_year", "selected_or_minutes",
                         "selected_buffer", "training_mean_r", "test_mean_r"]].to_dict("records"),
                      "combined": result["combined"], "bootstrap": result["bootstrap"],
                      "stability": result["stability"], "supported": result["supported"],
                      "holdout_touched": False, "report": str(report)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
