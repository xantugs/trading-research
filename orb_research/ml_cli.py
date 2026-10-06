"""Build the frozen, audited development-period event table; no model training."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .config import load_config
from .data_loader import load_m1
from .ml_dataset import audit_event_dataset, build_event_dataset
from .ml_report import dictionary_markdown, validation_markdown
from .walk_forward import validate_frozen_config


ROOT = Path(__file__).resolve().parents[1]


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
    if not (cache / "source.json").is_file():
        raise ValueError("cached development data has no broker source.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "walk_forward_base.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "ml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    validate_frozen_config(config)  # must precede every data read
    _require_cached_months(config)
    print("Loading cached FTMO M1 bars for 2018–2024 only...", flush=True)
    m1, provenance = load_m1(config)
    print(f"Loaded {len(m1):,} M1 bars through {m1.index[-1]}", flush=True)
    frame, generation = build_event_dataset(m1, config, emit=lambda msg: print(msg, flush=True))
    audit = audit_event_dataset(frame, m1, config)
    audit["config"] = str(args.config.resolve())
    audit["source"] = {key: provenance.get(key) for key in ("server", "symbol", "chart_mode")}
    audit["generation"] = generation
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    audit_path = output / "orb_event_feature_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, default=str) + "\n", encoding="utf-8")
    if not audit["passed"]:
        raise ValueError(f"event-feature leakage audit failed ({audit['error_count']} errors); see {audit_path}")
    csv_path = output / "orb_event_features.csv"
    frame.to_csv(csv_path, index=False)
    (output / "orb_event_features_dictionary.md").write_text(dictionary_markdown(), encoding="utf-8")
    (output / "orb_event_features_validation.md").write_text(validation_markdown(frame), encoding="utf-8")
    print(f"Built {len(frame):,} candidate rows from {frame.session_id.nunique():,} sessions; audit passed.", flush=True)
    print(f"Dataset: {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
