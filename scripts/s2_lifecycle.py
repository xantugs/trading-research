#!/usr/bin/env python3
"""12-month FTMO lifecycle for S2: net dollars per month per $100k account (docs/S2_FTMO_SIZING.md).

    python -m scripts.s2_lifecycle
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.s2_ftmo_sizing import scenarios

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/s2_sizing"
HORIZON, MONTH, SIZE, PATHS = 252, 21, 100_000, 1000
PLANS = {
    "1-Step": {"fee": 499.0, "split": 0.90, "daily": 0.03, "trailing": True, "targets": (0.10,),
               "lc": (1.0, 1.5, 2.0, 2.5, 3.0)},
    "2-Step": {"fee": 540.0, "split": 0.80, "daily": 0.05, "trailing": False, "targets": (0.10, 0.05),
               "lc": (1.0, 2.0, 3.0, 4.0)},
}
LF = (0.5, 1.0, 1.5, 2.0)


def life(days: np.ndarray, plan: dict, lc: float, lf: float, rng: np.random.Generator) -> tuple[float, int | None]:
    """Net dollars after 12 months, and the trading day of the first payout (None if none)."""
    t, net, first_payout = 0, 0.0, None
    while t < HORIZON:
        net -= plan["fee"]
        passed = True
        for target in plan["targets"]:                       # challenge phase(s) on fresh accounts
            bal, hwm, profits, done = 1.0, 1.0, [], False
            while t < HORIZON and not done:
                ret, worst = days[rng.integers(len(days))]
                t += 1
                hwm = max(hwm, bal) if plan["trailing"] else 1.0
                low = bal * (1 + lc * worst)
                if low <= bal * (1 - plan["daily"]) or low <= hwm - 0.10:
                    passed, done = False, True
                    break
                new = bal * (1 + lc * ret)
                profits.append(new - bal)
                bal = new
                best_ok = not plan["trailing"] or max(profits) <= 0.5 * sum(p for p in profits if p > 0)
                done = bal >= 1 + target and best_ok
            if not (passed and done):
                passed = False
                break
        if not passed:
            continue                                          # buy another challenge
        bal, hwm, refunded, day_in_month = 1.0, 1.0, False, 0
        while t < HORIZON:                                   # funded account
            ret, worst = days[rng.integers(len(days))]
            t += 1
            day_in_month += 1
            hwm = max(hwm, bal) if plan["trailing"] else 1.0
            low = bal * (1 + lf * worst)
            if low <= bal * (1 - plan["daily"]) or low <= hwm - 0.10:
                break                                         # account lost: start again
            bal *= 1 + lf * ret
            if day_in_month >= MONTH:
                day_in_month = 0
                if bal > 1.0:
                    net += plan["split"] * (bal - 1.0) * SIZE + (0 if refunded else plan["fee"])
                    refunded = True
                    first_payout = first_payout if first_payout is not None else t
                    bal, hwm = 1.0, 1.0
    return net, first_payout


def main() -> int:
    data = scenarios()
    rows = []
    for scenario, days in data.items():
        for name, plan in PLANS.items():
            for lc in plan["lc"]:
                for lf in LF:
                    rng = np.random.default_rng(41)
                    sims = [life(days, plan, lc, lf, rng) for _ in range(PATHS)]
                    net = np.array([s[0] for s in sims])
                    first = [s[1] for s in sims if s[1] is not None]
                    rows.append({"scenario": scenario, "account": name, "Lc": lc, "Lf": lf,
                                 "mean net per month": net.mean() / 12, "median net per month": np.median(net) / 12,
                                 "P(12 months below zero)": float((net < 0).mean()),
                                 "P(any payout)": len(first) / PATHS,
                                 "median months to first payout": float(np.median(first) / MONTH) if first else None})
            print(f"{scenario} {name} done", flush=True)
    table = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT / "lifecycle.csv", index=False)
    dev = table[table.scenario == "development edge"]
    chosen = {}
    for name in PLANS:
        best = dev[dev.account == name].sort_values("mean net per month", ascending=False).iloc[0]
        others = table[(table.account == name) & (table.Lc == best.Lc) & (table.Lf == best.Lf)]
        chosen[name] = {"Lc": float(best.Lc), "Lf": float(best.Lf),
                        **{row.scenario: {"mean net per month": round(row["mean net per month"], 1),
                                          "median net per month": round(row["median net per month"], 1),
                                          "P(12 months below zero)": round(row["P(12 months below zero)"], 3),
                                          "median months to first payout": row["median months to first payout"]}
                           for _, row in others.iterrows()}}
    (OUT / "lifecycle_chosen.json").write_text(json.dumps(chosen, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(chosen, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
