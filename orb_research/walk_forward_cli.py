"""Run the fixed 2018–2024 annual walk-forward research plan."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import load_config
from .data_loader import load_m1
from .walk_forward import run_walk_forward, validate_frozen_config, write_results


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "walk_forward_base.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--replication-symbol", default=None,
                        help="Replay the frozen plan unchanged on another broker symbol "
                             "(labelled replication; every other choice stays fixed)")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    expected_symbol = args.replication_symbol or "XAUUSD"
    validate_frozen_config(config, expected_symbol=expected_symbol)
    replication_of = "XAUUSD" if args.replication_symbol else None
    cache = Path(config.cache_dir) / config.expected_server / config.symbol
    month = datetime(2018, 1, 1)
    while month.year < 2025:
        path = cache / f"{month:%Y%m}.parquet"
        if not path.is_file():
            raise ValueError(f"cached development month missing: {path}")
        month = (month.replace(year=month.year + 1, month=1) if month.month == 12
                 else month.replace(month=month.month + 1))

    print("Loading cached FTMO development M1 bars through 2024 only...", flush=True)
    m1, provenance = load_m1(config)
    print(f"Loaded {len(m1):,} M1 bars; latest {m1.index[-1]}", flush=True)
    results = run_walk_forward(m1, config, emit=lambda message: print(message, flush=True),
                               expected_symbol=expected_symbol)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_dir or ROOT / "outputs" / "walk_forward" / f"{stamp}-{uuid4().hex[:6]}"
    if not output.is_absolute():
        output = ROOT / output
    report = write_results(output, results, config, provenance, replication_of)
    print(json.dumps({"report": str(report), "symbol": config.symbol,
                      "replication_of": replication_of, "combined": results["combined"],
                      "holdout_used": False}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
