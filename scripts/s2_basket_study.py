#!/usr/bin/env python3
"""S2 on a seven-market basket (docs/S2_BASKET_PLAN.md).

    python -m scripts.s2_basket_study clockcheck   # HistData clocks, absolute price moves only
    python -m scripts.s2_basket_study develop      # 2010-11 to 2017: gate and weights
    python -m scripts.s2_basket_study holdout      # once: HistData 2018-2024 and FTMO 2025-26
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import load_m1
from orb_research.intraday_momentum import s2_noise_area, session_grid

ROOT = Path(__file__).resolve().parents[1]
HISTDATA = Path("C:/data/histdata_m1/HistData")
OUT = ROOT / "outputs/s2_basket"
NY = "America/New_York"
MARKETS = {  # HistData symbol: FTMO config, exchange time zone, session open/close (minutes)
    "NSXUSD": ("configs/indices/ftmo_us100_orb_framework_15_15.json", NY, 570, 960),
    "SPXUSD": ("configs/symbols/ftmo_us500_cash.json", NY, 570, 960),
    "GRXEUR": ("configs/symbols/ftmo_ger40_cash.json", "Europe/Berlin", 540, 1050),
    "UKXGBP": ("configs/symbols/ftmo_uk100_cash.json", "Europe/London", 480, 990),
    "JPXJPY": ("configs/symbols/ftmo_jp225_cash.json", "Asia/Tokyo", 540, 900),
    "XAUUSD": ("configs/orb_framework.json", NY, 500, 810),
    "WTIUSD": ("configs/symbols/ftmo_usoil_cash.json", NY, 540, 870),
}
B_START, B_END = pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-10-07", tz="UTC")


def histdata_bars(symbol: str, start: str, end: str) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for p in sorted((HISTDATA / symbol).glob("*.parquet"))]
    m1 = pd.concat([f for f in frames if not f.empty]).sort_index()
    return m1.loc[(m1.index >= pd.Timestamp(start, tz="UTC")) & (m1.index < pd.Timestamp(end, tz="UTC"))]


def ftmo_bars(symbol: str) -> tuple[pd.DataFrame, np.ndarray]:
    config = load_config(ROOT / MARKETS[symbol][0])
    m1, _ = load_m1(config)
    m1 = m1.loc[(m1.index >= B_START) & (m1.index < B_END)]
    spread = m1.spread.to_numpy() * config.point
    fallback = np.median(spread[spread > 0]) if (spread > 0).any() else 0.0
    return m1, np.where(spread > 0, spread, fallback)


def spread_ratio(symbol: str) -> float:
    """FTMO's median in-session spread as a fraction of price, 2025-01 to 2026-10."""
    _, zone, open_m, close_m = MARKETS[symbol]
    m1, spread = ftmo_bars(symbol)
    local = m1.index.tz_convert(zone)
    minute = local.hour * 60 + local.minute
    keep = (minute >= open_m) & (minute < close_m)
    return float(np.median(spread[keep] / m1.close.to_numpy()[keep]))


def daily_s2(symbol: str, m1: pd.DataFrame, spread: np.ndarray) -> pd.DataFrame:
    _, zone, open_m, close_m = MARKETS[symbol]
    close, sp, first = session_grid(m1, spread, zone, open_m, close_m)
    out = s2_noise_area(close, sp, first)
    out.index = pd.to_datetime(pd.Series(out.index)).values
    return out[["trades", "ret"]]


def hist_daily(symbol: str, start: str, end: str, ratio: float) -> pd.DataFrame:
    m1 = histdata_bars(symbol, start, end)
    return daily_s2(symbol, m1, ratio * m1.close.to_numpy())


def stats(ret: pd.Series, trades: pd.Series) -> dict:
    values = ret.to_numpy(float)
    n, sd = len(values), values.std(ddof=1)
    traded = trades.to_numpy() > 0
    winning = pd.Series(ret[traded] > 0)
    weeks = winning.groupby(pd.DatetimeIndex(winning.index).to_period("W")).agg(["sum", "size"])
    months = ret.groupby(pd.DatetimeIndex(ret.index).to_period("M")).sum()
    yearly = ret.groupby(pd.DatetimeIndex(ret.index).year).mean()
    return {"days": n, "days_traded": int(traded.sum()), "mean_bps": float(values.mean() * 1e4),
            "t": float(values.mean() / sd * np.sqrt(n)) if sd > 0 else None,
            "sharpe": float(values.mean() / sd * np.sqrt(252)) if sd > 0 else None,
            "daily_sd_bps": float(sd * 1e4),
            "winning_days_of_traded": float(winning.mean()) if len(winning) else None,
            "weeks_with_3plus_wins": float((weeks["sum"] >= 3).mean()) if len(weeks) else None,
            "winning_months": float((months[months != 0] > 0).mean()) if (months != 0).any() else None,
            "avg_win_bps": float(ret[ret > 0].mean() * 1e4) if (ret > 0).any() else None,
            "avg_loss_bps": float(ret[ret < 0].mean() * 1e4) if (ret < 0).any() else None,
            "usd_per_day_10k": {f"{lev}x": float(values.mean() * lev * 10_000) for lev in (1, 2, 4)},
            "yearly_bps": {int(y): float(v * 1e4) for y, v in yearly.items()}}


def basket(dailies: dict[str, pd.DataFrame], weights: dict[str, float]) -> tuple[pd.Series, pd.Series]:
    rets = pd.DataFrame({s: d.ret for s, d in dailies.items() if s in weights}).fillna(0.0)
    trades = pd.DataFrame({s: d.trades for s, d in dailies.items() if s in weights}).fillna(0)
    combined = sum(rets[s] * w for s, w in weights.items())
    return combined.sort_index(), trades.sum(axis=1).sort_index()


def clockcheck() -> int:
    report = {}
    for symbol, (_, zone, open_m, _close) in MARKETS.items():
        m1 = histdata_bars(symbol, "2010-01-01", "2025-01-01")
        local = m1.index.tz_convert(zone)
        minute = local.hour * 60 + local.minute
        gap_ok = np.r_[False, np.diff(m1.index.asi8) == 60_000_000_000]
        move = np.abs(np.r_[np.nan, np.diff(m1.close.to_numpy())] / m1.close.to_numpy())
        frame = pd.DataFrame({"move": np.where(gap_ok, move, np.nan), "minute": minute, "year": local.year,
                              "summer": [bool(t.dst()) for t in local]})
        frame = frame[(frame.minute >= open_m - 60) & (frame.minute < open_m + 60)]
        cells = []
        for (year, summer), g in frame.groupby(["year", "summer"]):
            profile = g.groupby("minute").move.mean()
            jump = int((profile / profile.shift(1)).idxmax())
            cells.append({"year": int(year), "summer": bool(summer), "jump_minute": jump,
                          "ok": abs(jump - open_m) <= 2})
        share = float(np.mean([c["ok"] for c in cells]))
        report[symbol] = {"share_ok": share, "pass": share >= 0.7, "cells": cells}
        print(f"{symbol}: open {open_m // 60:02d}:{open_m % 60:02d} {zone}: jump at the open in {share:.0%} "
              f"of year-seasons -> {'pass' if share >= 0.7 else 'DROP'}  "
              + " ".join(f"{c['year']}{'S' if c['summer'] else 'W'}:{c['jump_minute'] // 60:02d}:{c['jump_minute'] % 60:02d}"
                         for c in cells if not c["ok"])[:300], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "clockcheck.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


def develop() -> int:
    clock = json.loads((OUT / "clockcheck.json").read_text(encoding="utf-8"))
    ratios, dev, rows = {}, {}, {}
    for symbol in MARKETS:
        if not clock[symbol]["pass"]:
            print(f"{symbol}: dropped by the clock check")
            continue
        ratios[symbol] = spread_ratio(symbol)
        dev[symbol] = hist_daily(symbol, "2010-01-01", "2018-01-01", ratios[symbol])
        rows[symbol] = stats(dev[symbol].ret, dev[symbol].trades)
        r = rows[symbol]
        print(f"{symbol}: spread {ratios[symbol] * 1e4:.2f} bps  dev {r['mean_bps']:+.2f} bps/day t={r['t']:+.2f} "
              f"Sharpe={r['sharpe']:+.2f} sd={r['daily_sd_bps']:.0f} wins={r['winning_days_of_traded']:.0%}", flush=True)
    gate = [s for s in dev if rows[s]["mean_bps"] > 0]
    inverse = {s: 1.0 / dev[s].ret.std(ddof=1) for s in gate}
    raw, _ = basket(dev, inverse)
    scale = dev["NSXUSD"].ret.std(ddof=1) / raw.std(ddof=1)
    weights = {s: w * scale for s, w in inverse.items()}
    combined, trades = basket(dev, weights)
    rows["BASKET"] = stats(combined, trades)
    r = rows["BASKET"]
    print(f"BASKET {gate}: dev {r['mean_bps']:+.2f} bps/day t={r['t']:+.2f} Sharpe={r['sharpe']:+.2f} "
          f"wins={r['winning_days_of_traded']:.0%} weeks3+={r['weeks_with_3plus_wins']:.0%} months={r['winning_months']:.0%}")
    (OUT / "development.json").write_text(json.dumps({"spread_ratio": ratios, "gate": gate, "weights": weights,
                                                      "results": rows}, indent=2) + "\n", encoding="utf-8")
    return 0


def holdout() -> int:
    target = OUT / "holdout.json"
    if target.exists():
        raise SystemExit(f"the holdout has already been run once: {target}")
    dev = json.loads((OUT / "development.json").read_text(encoding="utf-8"))
    weights, ratios = dev["weights"], dev["spread_ratio"]
    out = {}
    for label in ("A", "B"):
        dailies = {}
        for symbol in weights:
            if label == "A":
                dailies[symbol] = hist_daily(symbol, "2018-01-01", "2025-01-01", ratios[symbol])
            else:
                m1, spread = ftmo_bars(symbol)
                dailies[symbol] = daily_s2(symbol, m1, spread)
        results = {s: stats(d.ret, d.trades) for s, d in dailies.items()}
        combined, trades = basket(dailies, weights)
        results["BASKET"] = stats(combined, trades)
        out[label] = results
        for name, r in results.items():
            print(f"holdout {label} {name:7s} {r['mean_bps']:+6.2f} bps/day t={r['t']:+.2f} Sharpe={r['sharpe']:+.2f} "
                  f"wins={r['winning_days_of_traded']:.0%} weeks3+={r['weeks_with_3plus_wins']:.0%} "
                  f"months={r['winning_months']:.0%} $/day@4x={r['usd_per_day_10k']['4x']:+.1f}", flush=True)
    a, b = out["A"]["BASKET"], out["B"]["BASKET"]
    years_up = sum(v > 0 for y, v in a["yearly_bps"].items() if 2018 <= y <= 2024)
    passed = a["mean_bps"] > 0 and (a["t"] or 0) >= 2.3 and years_up >= 6
    out["verdict"] = ("pass" if b["mean_bps"] > 0 else "pass A, not confirmed by B") if passed else "fail"
    out["basket_years_positive_A"] = years_up
    target.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print("verdict:", out["verdict"], f"(A years positive {years_up}/7)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("clockcheck", "develop", "holdout"))
    return {"clockcheck": clockcheck, "develop": develop, "holdout": holdout}[parser.parse_args(argv).step]()


if __name__ == "__main__":
    raise SystemExit(main())
