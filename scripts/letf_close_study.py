#!/usr/bin/env python3
"""Run docs/LETF_CLOSE_PLAN.md once: L1 on US100 and US500.

    python -m scripts.letf_close_study
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import load_m1
from orb_research.intraday_momentum import session_grid
from orb_research.regime_strategies import l1_letf_close
from scripts.s2_basket_study import histdata_bars, spread_ratio, stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/letf_close"
MARKETS = {"US100": ("configs/indices/ftmo_us100_orb_framework_15_15.json", "NSXUSD"),
           "US500": ("configs/symbols/ftmo_us500_cash.json", "SPXUSD")}
START, END = pd.Timestamp("2021-10-01", tz="UTC"), pd.Timestamp("2026-10-07", tz="UTC")


def daily(m1: pd.DataFrame, spread: np.ndarray) -> pd.DataFrame:
    close, sp, _ = session_grid(m1, spread)
    out = l1_letf_close(close, sp)
    out.index = pd.to_datetime(pd.Series(out.index)).values
    return out


def summarise(frame: pd.DataFrame) -> dict:
    r = stats(frame.ret, frame.trades)
    traded = frame.trades > 0
    r["win_rate_traded"] = float((frame.ret[traded] > 0).mean()) if traded.any() else None
    return r


def main() -> int:
    target = OUT / "results.json"
    if target.exists():
        raise SystemExit(f"the study has already been run once: {target}")
    results = {}
    for market, (config_path, hist_symbol) in MARKETS.items():
        config = load_config(ROOT / config_path)
        m1, _ = load_m1(config)
        m1 = m1.loc[(m1.index >= START) & (m1.index < END)]
        recorded = m1.spread.to_numpy() * config.point
        spread = np.where(recorded > 0, recorded, np.median(recorded[recorded > 0]))
        ftmo = daily(m1, spread)
        hist_m1 = histdata_bars(hist_symbol, "2010-01-01", "2021-01-01")
        hist = daily(hist_m1, spread_ratio(hist_symbol) * hist_m1.close.to_numpy())
        periods = {"FTMO 2021-10..2026-10": ftmo,
                   "FTMO 2021-10..2023": ftmo[ftmo.index < pd.Timestamp("2024-01-01")],
                   "FTMO 2024..2026-10": ftmo[ftmo.index >= pd.Timestamp("2024-01-01")],
                   "HistData 2010-11..2020 (context)": hist}
        rows = {name: summarise(frame) for name, frame in periods.items()}
        main_, h1, h2 = rows["FTMO 2021-10..2026-10"], rows["FTMO 2021-10..2023"], rows["FTMO 2024..2026-10"]
        passed = main_["mean_bps"] > 0 and (main_["t"] or 0) >= 2.3 and h1["mean_bps"] > 0 and h2["mean_bps"] > 0
        results[market] = {"periods": rows, "pass": bool(passed)}
        for name, r in rows.items():
            print(f"{market} {name:34s} {r['mean_bps']:+.2f} bps/day t={r['t']:+.2f} traded={r['days_traded']}/{r['days']} "
                  f"win={r['win_rate_traded']:.0%} months={r['winning_months']:.0%} $/day@4x={r['usd_per_day_10k']['4x']:+.1f}",
                  flush=True)
        print(f"{market}: {'PASS' if passed else 'fail'}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
