#!/usr/bin/env python3
"""Scale the two price-unit cost parameters to a replication instrument's own spread.

Gold's slippage and fallback spread are absolute prices, so carrying them to a
cheaper instrument would misstate costs badly. The rule was fixed before any
replication data existed (see the pre-registration): scale both by the ratio of
median recorded spread, in price units, between this instrument and the XAUUSD
reference. Only the development cache is read, no outcome is involved, and the
config is rewritten in place.

    .venv\\Scripts\\python scripts\\finalize_replication_costs.py --config configs\\strategy_a_silver.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orb_research.config import load_config  # noqa: E402


REFERENCE = {"symbol": "XAUUSD", "median_spread_price": 0.15,
             "slippage_price": 0.02, "fallback_spread_price": 0.20}


def median_recorded_spread(config) -> tuple[float, int, int]:
    """Median recorded spread in price units over development months only."""
    cache = Path(config.cache_dir) / config.expected_server / config.symbol
    cutoff = config.development_end.replace("-", "")[:6]
    months = sorted(path for path in cache.glob("[0-9]" * 6 + ".parquet")
                    if path.stem < cutoff)
    if not months:
        raise ValueError(f"no cached development months under {cache}")
    spreads = pd.concat([pd.read_parquet(path, columns=["spread"]) for path in months])
    positive = spreads.spread[spreads.spread > 0]
    if positive.empty:
        raise ValueError("every cached bar records a zero spread; cannot scale costs")
    return float(positive.median()) * config.point, len(months), int((spreads.spread <= 0).sum())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference-median-spread", type=float,
                        default=REFERENCE["median_spread_price"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    median_price, months, zero_bars = median_recorded_spread(config)
    ratio = median_price / args.reference_median_spread

    def to_grid(value: float) -> float:
        """Round to the instrument's own price grid, never below one point."""
        steps = max(1, round(value / config.point))
        return round(steps * config.point, 10)

    slippage = to_grid(REFERENCE["slippage_price"] * ratio)
    fallback = to_grid(REFERENCE["fallback_spread_price"] * ratio)
    print(f"{config.symbol}: {months} development months, median recorded spread "
          f"{median_price:.5f} in price units ({zero_bars:,} zero-spread bars excluded)")
    print(f"Reference {REFERENCE['symbol']} median spread {args.reference_median_spread:.5f} "
          f"-> scaling ratio {ratio:.4f}")
    print(f"  slippage_price        {config.slippage_price} -> {slippage}")
    print(f"  fallback_spread_price {config.fallback_spread_price} -> {fallback}")
    if args.dry_run:
        print("Dry run; configuration unchanged.")
        return 0
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    raw["slippage_price"] = slippage
    raw["fallback_spread_price"] = fallback
    args.config.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    load_config(args.config)  # re-validate what we just wrote
    print(f"Updated {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
