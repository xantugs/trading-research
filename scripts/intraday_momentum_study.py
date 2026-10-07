#!/usr/bin/env python3
"""Run docs/INTRADAY_MOMENTUM_PLAN.md: S1 and S2 on the Nasdaq-100.

    python -m scripts.intraday_momentum_study develop   # HistData 2010-11 to 2017
    python -m scripts.intraday_momentum_study holdout   # once: HistData 2018-2024 and FTMO 2025-26
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.data_loader import load_m1
from orb_research.intraday_momentum import s1_last_half_hour, s2_noise_area, session_grid, summary
from scripts.pre2021_holdout import candidate, histdata

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/intraday_momentum"
SPREAD_RATIO = json.loads((ROOT / "outputs/pre2021_holdout/calibration.json").read_text(encoding="utf-8"))["spread_ratio"]


def run(m1: pd.DataFrame, spread_price: np.ndarray) -> dict:
    close, spread, first = session_grid(m1, spread_price)
    return {"S1": summary(s1_last_half_hour(close, spread)), "S2": summary(s2_noise_area(close, spread, first))}


def hist_period(start: str, end: str) -> dict:
    m1, config = histdata(start, end, SPREAD_RATIO)
    return run(m1, m1.spread.to_numpy() * config.point)


def show(label: str, results: dict) -> None:
    for name, r in results.items():
        print(f"{label:28s} {name}: {r['mean_bps']:+.2f} bps/day t={r['t']:+.2f} days={r['days']} "
              f"trades={r['trades']} hit={r['hit_rate']:.3f} Sharpe={r['sharpe']:+.2f}", flush=True)


def develop() -> int:
    results = hist_period("2010-01-01", "2018-01-01")
    show("development 2010-11..2017", results)
    gate = {name: r["mean_bps"] > 0 for name, r in results.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "development.json").write_text(json.dumps({"results": results, "to_holdout": gate}, indent=2) + "\n",
                                          encoding="utf-8")
    print("to holdout:", gate)
    return 0


def holdout() -> int:
    target = OUT / "holdout.json"
    if target.exists():
        raise SystemExit(f"the holdout has already been run once: {target}")
    gate = json.loads((OUT / "development.json").read_text(encoding="utf-8"))["to_holdout"]
    if not any(gate.values()):
        raise SystemExit("no strategy passed the development gate; by the plan there is no holdout run")
    a = hist_period("2018-01-01", "2025-01-01")
    base = candidate()
    ftmo, _ = load_m1(base)
    ftmo = ftmo.loc[ftmo.index >= pd.Timestamp("2025-01-01", tz="America/New_York")]
    recorded = ftmo.spread.to_numpy() * base.point
    b = run(ftmo, np.where(recorded > 0, recorded, base.fallback_spread_price))
    show("holdout A HistData 2018-24", a)
    show("holdout B FTMO 2025-26", b)
    verdict = {}
    for name in ("S1", "S2"):
        if not gate[name]:
            verdict[name] = "stopped at the development gate"
            continue
        ra, rb = a[name], b[name]
        years_up = sum(v > 0 for y, v in ra["yearly_bps"].items() if 2018 <= y <= 2024)
        passed_a = ra["mean_bps"] > 0 and (ra["t"] or 0) >= 2.3 and years_up >= 4
        verdict[name] = ("pass" if rb["mean_bps"] > 0 else "pass A, not confirmed by B") if passed_a else "fail"
    target.write_text(json.dumps({"A": a, "B": b, "verdict": verdict}, indent=2) + "\n", encoding="utf-8")
    print("verdict:", verdict)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("develop", "holdout"))
    return {"develop": develop, "holdout": holdout}[parser.parse_args(argv).step]()


if __name__ == "__main__":
    raise SystemExit(main())
