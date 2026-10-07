#!/usr/bin/env python3
"""Size S2 for FTMO's 2-Step and 1-Step rules (docs/S2_FTMO_SIZING.md).

    python -m scripts.s2_ftmo_sizing
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.data_loader import load_m1
from orb_research.intraday_momentum import s2_noise_area, session_grid
from scripts.intraday_momentum_study import SPREAD_RATIO
from scripts.pre2021_holdout import candidate, histdata

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/s2_sizing"
LEVERAGES = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
DAYS_PER_MONTH = 21
MAX_DAYS = 2520           # ten years per attempt
CHALLENGE_PATHS, FUNDED_PATHS = 1500, 5000


def daily(m1: pd.DataFrame, spread_price: np.ndarray) -> pd.DataFrame:
    close, spread, first = session_grid(m1, spread_price)
    return s2_noise_area(close, spread, first)[["ret", "worst"]]


def scenarios() -> dict[str, np.ndarray]:
    dev_m1, dev_cfg = histdata("2010-01-01", "2018-01-01", SPREAD_RATIO)
    dev = daily(dev_m1, dev_m1.spread.to_numpy() * dev_cfg.point)
    hold_m1, hold_cfg = histdata("2018-01-01", "2025-01-01", SPREAD_RATIO)
    hold = daily(hold_m1, hold_m1.spread.to_numpy() * hold_cfg.point)
    base = candidate()
    ftmo, _ = load_m1(base)
    ftmo = ftmo.loc[ftmo.index >= pd.Timestamp("2025-01-01", tz="America/New_York")]
    recorded = ftmo.spread.to_numpy() * base.point
    late = daily(ftmo, np.where(recorded > 0, recorded, base.fallback_spread_price))
    holdout = pd.concat([hold, late])
    zero = holdout - holdout.ret.mean()  # shifts the day's low and close together
    return {name: frame[["ret", "worst"]].to_numpy() for name, frame in
            (("development edge", dev), ("holdout edge", holdout), ("zero edge", zero))}


def _phase(days, lev, rng, target: float):
    """One 2-Step phase on a fresh account: 5% daily and static 10% limits on marked equity."""
    bal, n = 1.0, 0
    while n < MAX_DAYS:
        ret, worst = days[rng.integers(len(days))]
        n += 1
        low = bal * (1 + lev * worst)
        if low <= bal * 0.95 or low <= 0.90:
            return False, n
        bal *= 1 + lev * ret
        if bal >= 1 + target:
            return True, n
    return False, n


def challenge_2step(days, lev, rng):
    ok, first = _phase(days, lev, rng, 0.10)
    if not ok:
        return False, first
    ok, second = _phase(days, lev, rng, 0.05)
    return ok, first + second


def challenge_1step(days, lev, rng):
    bal, hwm, profits, n = 1.0, 1.0, [], 0
    while n < MAX_DAYS:
        ret, worst = days[rng.integers(len(days))]
        n += 1
        hwm = max(hwm, bal)
        low = bal * (1 + lev * worst)
        if low <= bal * 0.97 or low <= hwm - 0.10:
            return False, n
        new = bal * (1 + lev * ret)
        profits.append(new - bal)
        bal = new
        if bal >= 1.10 and max(profits) <= 0.5 * sum(p for p in profits if p > 0):
            return True, n
    return False, n


def to_funded(days, lev, rng, attempt):
    total = 0
    for k in range(1, 11):
        ok, n = attempt(days, lev, rng)
        total += n
        if ok:
            return k, total, True
    return 10, total, False


def funded_year(days, lev, rng, trailing: bool, daily_limit: float):
    bal, hwm = 1.0, 1.0
    for idx in rng.integers(len(days), size=12 * DAYS_PER_MONTH):
        ret, worst = days[idx]
        hwm = max(hwm, bal) if trailing else 1.0
        low = bal * (1 + lev * worst)
        if low <= bal * (1 - daily_limit) or low <= hwm - 0.10:
            return True, bal - 1
        bal *= 1 + lev * ret
    return False, bal - 1


def main() -> int:
    data = scenarios()
    rows = []
    for name, days in data.items():
        for lev in LEVERAGES:
            row = {"scenario": name, "leverage": lev}
            for label, attempt in (("2-Step", challenge_2step), ("1-Step", challenge_1step)):
                rng = np.random.default_rng(31)
                sims = [to_funded(days, lev, rng, attempt) for _ in range(CHALLENGE_PATHS)]
                row[f"{label} first-try pass"] = float(np.mean([s[0] == 1 and s[2] for s in sims]))
                row[f"{label} mean attempts"] = float(np.mean([s[0] for s in sims]))
                row[f"{label} median months to funded"] = float(np.median([s[1] for s in sims]) / DAYS_PER_MONTH)
            for label, trailing, limit in (("2-Step funded", False, 0.05), ("1-Step funded", True, 0.03)):
                rng = np.random.default_rng(37)
                year = [funded_year(days, lev, rng, trailing, limit) for _ in range(FUNDED_PATHS)]
                row[f"{label} P(lose in 1y)"] = float(np.mean([y[0] for y in year]))
                row[f"{label} median 1y return"] = float(np.median([y[1] for y in year]))
            rows.append(row)
            print(f"{name:16s} L={lev:<4} " + "  ".join(f"{k}={v:.3f}" for k, v in row.items()
                                                       if k not in ("scenario", "leverage")), flush=True)
    table = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT / "results.csv", index=False)
    dev, zero = table[table.scenario == "development edge"], table[table.scenario == "zero edge"].set_index("leverage")
    chosen = {}
    for step in ("2-Step", "1-Step"):
        ok = dev[dev[f"{step} funded P(lose in 1y)"] <= 0.10]
        chosen[f"{step} funded leverage"] = float(ok.leverage.max()) if len(ok) else None
        fast = dev[(dev[f"{step} mean attempts"] <= 3) &
                   (dev.leverage.map(lambda v: zero.at[v, f"{step} mean attempts"]) <= 5)]
        chosen[f"{step} fast challenge leverage"] = (
            float(fast.sort_values([f"{step} median months to funded", "leverage"]).leverage.iloc[0]) if len(fast) else None)
        safe = dev.sort_values([f"{step} first-try pass", "leverage"], ascending=False)
        chosen[f"{step} safe challenge leverage"] = float(safe.leverage.iloc[0])
    (OUT / "chosen.json").write_text(json.dumps(chosen, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(chosen, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
