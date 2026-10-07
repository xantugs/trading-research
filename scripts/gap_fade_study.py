#!/usr/bin/env python3
"""Run docs/GAP_FADE_PLAN.md: the opening-gap fade on the Nasdaq-100, alone and with S2.

    python -m scripts.gap_fade_study develop    # HistData 2010-11 to 2017
    python -m scripts.gap_fade_study holdout    # once: HistData 2018-2024 and FTMO 2025-26
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.data_loader import load_m1
from orb_research.intraday_momentum import gap_fade, s2_noise_area, session_grid
from scripts.intraday_momentum_study import SPREAD_RATIO
from scripts.pre2021_holdout import candidate, histdata
from scripts.s2_basket_study import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/gap_fade"


def dailies(m1: pd.DataFrame, spread: np.ndarray) -> dict[str, pd.DataFrame]:
    close, sp, first = session_grid(m1, spread)
    fade, s2 = gap_fade(close, sp, first), s2_noise_area(close, sp, first)
    for frame in (fade, s2):
        frame.index = pd.to_datetime(pd.Series(frame.index)).values
    both = pd.DataFrame({"fade": fade.ret, "s2": s2.ret}).fillna(0.0)
    trades = pd.DataFrame({"fade": fade.trades, "s2": s2.trades}).fillna(0)
    combined = pd.DataFrame({"ret": both.sum(axis=1), "trades": trades.sum(axis=1)})
    return {"fade": fade, "s2": s2, "s2 + fade": combined}


def report(label: str, frames: dict[str, pd.DataFrame]) -> dict:
    out = {}
    for name, d in frames.items():
        r = stats(d.ret, d.trades)
        win_traded = float((d.ret[d.trades > 0] > 0).mean()) if (d.trades > 0).any() else None
        r["win_rate_traded"] = win_traded
        out[name] = r
        print(f"{label:24s} {name:10s} {r['mean_bps']:+6.2f} bps/day t={r['t']:+.2f} Sharpe={r['sharpe']:+.2f} "
              f"traded={r['days_traded']}/{r['days']} wins={win_traded:.0%} weeks3+={r['weeks_with_3plus_wins']:.0%} "
              f"months={r['winning_months']:.0%} $/day@4x={r['usd_per_day_10k']['4x']:+.1f}", flush=True)
    return out


def hist(start: str, end: str) -> dict[str, pd.DataFrame]:
    m1, config = histdata(start, end, SPREAD_RATIO)
    return dailies(m1, m1.spread.to_numpy() * config.point)


def develop() -> int:
    results = report("development 2010-11..2017", hist("2010-01-01", "2018-01-01"))
    gate = results["fade"]["mean_bps"] > 0
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "development.json").write_text(json.dumps({"results": results, "to_holdout": gate}, indent=2) + "\n",
                                          encoding="utf-8")
    print("to holdout:", gate)
    return 0


def holdout() -> int:
    target = OUT / "holdout.json"
    if target.exists():
        raise SystemExit(f"the holdout has already been run once: {target}")
    if not json.loads((OUT / "development.json").read_text(encoding="utf-8"))["to_holdout"]:
        raise SystemExit("the fade failed the development gate; by the plan there is no holdout run")
    a = report("holdout A HistData 2018-24", hist("2018-01-01", "2025-01-01"))
    base = candidate()
    ftmo, _ = load_m1(base)
    ftmo = ftmo.loc[ftmo.index >= pd.Timestamp("2025-01-01", tz="America/New_York")]
    recorded = ftmo.spread.to_numpy() * base.point
    b = report("holdout B FTMO 2025-26", dailies(ftmo, np.where(recorded > 0, recorded, base.fallback_spread_price)))
    fa = a["fade"]
    years_up = sum(v > 0 for y, v in fa["yearly_bps"].items() if 2018 <= y <= 2024)
    passed = fa["mean_bps"] > 0 and (fa["t"] or 0) >= 2.0 and years_up >= 4
    verdict = ("pass" if b["fade"]["mean_bps"] > 0 else "pass A, not confirmed by B") if passed else "fail"
    target.write_text(json.dumps({"A": a, "B": b, "fade_years_positive_A": years_up, "verdict": verdict},
                                 indent=2) + "\n", encoding="utf-8")
    print("verdict:", verdict, f"(fade years positive in A: {years_up}/7)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("develop", "holdout"))
    return {"develop": develop, "holdout": holdout}[parser.parse_args(argv).step]()


if __name__ == "__main__":
    raise SystemExit(main())
