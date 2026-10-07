#!/usr/bin/env python3
"""Run the tweak study of docs/US100_TWEAK_STUDY.md.

Development mode loads bars only to 2025-01-01 (the config guard refuses later data),
runs the eight pre-declared tweaks on period A (2021-10 to 2023) and B (2024), applies
the adoption rule and records the combined set.  ``--final`` then runs that set once on
2025-01-01 to 2026-10-06 and refuses to run a second time.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from orb_research.backtester import backtest
from orb_research.config import load_config
from orb_research.data_loader import load_m1

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs/indices/ftmo_us100_orb_framework_15_15.json"
OUT = ROOT / "outputs/indices/us100_tweaks"
TWEAKS = {
    "T1 trend filter (50-day SMA)": {"trend_sma_days": 50},
    "T2 breakeven at +1R": {"breakeven_r": 1.0},
    "T3 gap filter (0.5 x ATR)": {"gap_max_atr": 0.5},
    "T4 range 1.25-3 x ATR(1344)": {"or_atr_period": 1344, "or_min_atr": 1.25, "or_max_atr": 3.0},
    "T5 first breakout only": {"trades_per_session": "one"},
    "T6 flat at 16:00": {"position_exit": "end_of_day", "flat_time": "16:00"},
    "T7 5-minute entries": {"signal_minutes": 5},
    "T8 long only": {"trade_direction": "long"},
}
A_END = pd.Timestamp("2024-01-01", tz="America/New_York")
B_END = pd.Timestamp("2025-01-01", tz="America/New_York")


def periods(trades: pd.DataFrame) -> dict:
    stamp = pd.to_datetime(trades.timestamp, utc=True)
    out = {}
    for name, mask in (("A", stamp < A_END), ("B", (stamp >= A_END) & (stamp < B_END)),
                       ("final", stamp >= B_END)):
        r = trades.R_result[mask]
        out[name] = {"trades": int(len(r)), "mean_r": float(r.mean()) if len(r) else None}
    return out


def development() -> int:
    base = load_config(CANDIDATE)
    # Re-seal: the guard in Config.validate refuses any bar from 2025-01-01 on.
    dev = replace(base, development_end="2025-01-01", holdout_opened=None)
    dev.validate()
    m1, _ = load_m1(dev)
    assert m1.index.max() < pd.Timestamp("2025-01-01", tz="UTC")
    print(f"Loaded {len(m1):,} M1 bars, last {m1.index.max()} (2025-26 sealed)", flush=True)
    baseline = periods(backtest(m1, dev)[0])
    results = {"baseline": baseline, "tweaks": {}}
    for name, overrides in TWEAKS.items():
        stats = periods(backtest(m1, replace(dev, **overrides))[0])
        adopt = all(stats[p]["mean_r"] is not None and stats[p]["mean_r"] > baseline[p]["mean_r"]
                    and stats[p]["trades"] >= 0.5 * baseline[p]["trades"] for p in ("A", "B")) \
            and stats["B"]["mean_r"] > 0
        results["tweaks"][name] = {"overrides": overrides, **{p: stats[p] for p in ("A", "B")}, "adopted": adopt}
        print(f"  {name:32s} A {stats['A']['mean_r']:+.4f} ({stats['A']['trades']})  "
              f"B {stats['B']['mean_r']:+.4f} ({stats['B']['trades']})  adopted: {adopt}", flush=True)
    adopted = {k: v["overrides"] for k, v in results["tweaks"].items() if v["adopted"]}
    combined = {key: value for overrides in adopted.values() for key, value in overrides.items()}
    results["adopted"] = list(adopted)
    results["combined_overrides"] = combined
    if combined:
        stats = periods(backtest(m1, replace(dev, **combined))[0])
        results["combined"] = {p: stats[p] for p in ("A", "B")}
        print(f"  combined {list(adopted)}: A {stats['A']['mean_r']:+.4f} ({stats['A']['trades']})  "
              f"B {stats['B']['mean_r']:+.4f} ({stats['B']['trades']})", flush=True)
    print(f"Baseline: A {baseline['A']['mean_r']:+.4f} ({baseline['A']['trades']})  "
          f"B {baseline['B']['mean_r']:+.4f} ({baseline['B']['trades']})")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev_results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(OUT / "dev_results.json")
    return 0


def final() -> int:
    dev_results = json.loads((OUT / "dev_results.json").read_text(encoding="utf-8"))
    target = OUT / "final_results.json"
    if target.exists():
        raise SystemExit(f"the final test has already run once: {target}")
    combined = dev_results.get("combined_overrides") or {}
    if not combined:
        raise SystemExit("no tweak was adopted; by the plan there is no final test")
    base = load_config(CANDIDATE)  # full window to 2026-10-07, holdout opened
    m1, _ = load_m1(base)
    final_stats = periods(backtest(m1, replace(base, **combined))[0])["final"]
    baseline_stats = periods(backtest(m1, base)[0])["final"]
    passed = final_stats["mean_r"] is not None and final_stats["mean_r"] > 0 \
        and final_stats["mean_r"] > baseline_stats["mean_r"]
    result = {"combined_overrides": combined, "final": final_stats, "baseline_final": baseline_stats,
              "passed": passed}
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Final (2025-01-01 to 2026-10-06): tweaked {final_stats['mean_r']:+.4f} ({final_stats['trades']}) vs "
          f"baseline {baseline_stats['mean_r']:+.4f} ({baseline_stats['trades']}) -> passed: {passed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final", action="store_true", help="run the one final test on 2025-26")
    args = parser.parse_args(argv)
    return final() if args.final else development()


if __name__ == "__main__":
    raise SystemExit(main())
