#!/usr/bin/env python3
"""Run docs/REGIME_STRATEGIES_PLAN.md once: R2, R3 and R4 on US100 and US500, 2010-2026.

    python -m scripts.regime_study
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from orb_research.intraday_momentum import session_grid
from orb_research.regime_strategies import r2_calm_fade, r3_late_reversal, r4_overnight_long
from scripts.s2_basket_study import ftmo_bars, histdata_bars, spread_ratio, stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/regime_study"
MARKETS = {"US100": ("NSXUSD", 2.13e-4), "US500": ("SPXUSD", 2.23e-4)}   # HistData symbol, swap per night
PERIODS = {"2010-22 (before 0DTE)": ("2010-01-01", "2023-01-01"), "2023-24": ("2023-01-01", "2025-01-01"),
           "2025-26 FTMO": ("2025-01-01", "2026-10-07"), "0DTE era 2023-26": ("2023-01-01", "2026-10-07")}


def run_rules(m1: pd.DataFrame, spread, swap: float) -> dict[str, pd.DataFrame]:
    close, sp, first = session_grid(m1, spread)
    out = {"R2 calm-day fade": r2_calm_fade(close, sp, first), "R3 late-day reversal": r3_late_reversal(close, sp, first),
           "R4 overnight long": r4_overnight_long(close, sp, first, swap)}
    for frame in out.values():
        frame.index = pd.to_datetime(pd.Series(frame.index)).values
    return out


def main() -> int:
    target = OUT / "results.json"
    if target.exists():
        raise SystemExit(f"the study has already been run once: {target}")
    results = {}
    for market, (hist_symbol, swap) in MARKETS.items():
        ratio = spread_ratio(hist_symbol)
        hist = histdata_bars(hist_symbol, "2010-01-01", "2025-01-01")
        ftmo, ftmo_spread = ftmo_bars(hist_symbol)
        a = run_rules(hist, ratio * hist.close.to_numpy(), swap)
        b = run_rules(ftmo, ftmo_spread, swap)
        for rule in a:
            daily = pd.concat([a[rule], b[rule]]).sort_index()
            rows = {}
            for period, (start, end) in PERIODS.items():
                part = daily[(daily.index >= pd.Timestamp(start)) & (daily.index < pd.Timestamp(end))]
                r = stats(part.ret, part.trades)
                traded = part.trades > 0
                r["win_rate_traded"] = float((part.ret[traded] > 0).mean()) if traded.any() else None
                rows[period] = r
            era, e1, e2 = rows["0DTE era 2023-26"], rows["2023-24"], rows["2025-26 FTMO"]
            passed = era["mean_bps"] > 0 and (era["t"] or 0) >= 2.5 and e1["mean_bps"] > 0 and e2["mean_bps"] > 0
            results[f"{rule} | {market}"] = {"periods": rows, "pass": bool(passed)}
            line = "  ".join(f"{p}: {r['mean_bps']:+.2f} bps t={r['t']:+.2f} win={r['win_rate_traded']:.0%}"
                             for p, r in rows.items())
            print(f"{rule:22s} {market}: {'PASS' if passed else 'fail'} | {line}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
