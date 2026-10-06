"""Describe fixed event-time features on 2018–2024 development events only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import load_config
from .feature_analysis import analyze_features
from .feature_analysis_report import build_report
from .walk_forward import validate_frozen_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "walk_forward_base.json"
DEFAULT_INPUT = ROOT / "outputs" / "ml" / "orb_event_features.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="Frozen research config; defaults to configs/walk_forward_base.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "ml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    validate_frozen_config(config)
    source = DEFAULT_INPUT
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    if not source.is_file():
        raise ValueError(f"audited event dataset missing: {source}; run build-ml-dataset first")
    audit_path = source.parent / "orb_event_feature_audit.json"
    if not audit_path.is_file():
        raise ValueError("event feature audit missing; run build-ml-dataset first")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    latest = pd.to_datetime(audit.get("max_market_timestamp_utc"), utc=True, errors="raise")
    if (audit.get("passed") is not True or audit.get("holdout_used") is not False or
            pd.isna(latest) or latest >= pd.Timestamp("2025-01-01", tz="UTC") or
            audit.get("source", {}).get("server") != config.expected_server or
            audit.get("source", {}).get("symbol") != config.symbol):
        raise ValueError("event feature audit does not certify sealed development data")
    print(f"Reading existing development event table: {source}", flush=True)
    raw = pd.read_csv(source)  # exactly one DataFrame load; no M1, tick or MT5 access
    if len(raw) != audit.get("event_rows"):
        raise ValueError("event CSV row count disagrees with its leakage audit")
    print(f"Loaded {len(raw):,} candidate rows; validating dates, labels and sessions...", flush=True)
    result = analyze_features(raw, bootstrap_samples=10_000, seed=config.random_seed)
    print("Fixed bins and 10,000 session-block bootstrap draws complete.", flush=True)
    output.mkdir(parents=True, exist_ok=True)
    names = {
        "bins": "feature_bins.csv", "stability": "feature_stability.csv",
        "pairwise": "feature_pairwise_summary.csv", "group_effects": "feature_group_effects.csv",
        "year_buffer": "feature_year_buffer.csv", "correlations": "feature_correlations.csv",
        "bootstrap": "feature_bootstrap.csv",
    }
    for key, filename in names.items():
        result[key].to_csv(output / filename, index=False)
    report = build_report(result, source_name=source.name, bootstrap_samples=10_000)
    (output / "feature_stability_report.md").write_text(report, encoding="utf-8")
    print(f"Feature stability report: {output / 'feature_stability_report.md'}", flush=True)
    print(f"Rows: {len(result['events']):,}; unique sessions: {result['events'].session_id.nunique():,}; "
          "2025+ read: no; trading rules created: none", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
