#!/usr/bin/env python3
"""The pre-2021 Nasdaq-100 holdout study of docs/PRE2021_HOLDOUT_PLAN.md.

    python -m scripts.pre2021_holdout calibrate   # overlap 2021-10 to 2024 only
    python -m scripts.pre2021_holdout develop     # FTMO 2021-10 to 2026-10 only
    python -m scripts.pre2021_holdout holdout     # once: HistData 2010-01 to 2021-09
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.backtester import backtest
from orb_research.config import load_config
from orb_research.data_loader import load_m1

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs/indices/ftmo_us100_orb_framework_15_15.json"
HISTDATA = Path("C:/data/histdata_m1")
OUT = ROOT / "outputs/pre2021_holdout"
NY = "America/New_York"
FAMILIES = {
    "D1 range-size filter": [{"or_max_atr": v} for v in (2.0, 2.5, 3.0)],
    "D2 trend filter": [{"trend_sma_days": v} for v in (20, 50, 100)],
    "D3 quicker": [{"signal_minutes": 5}, {"trades_per_session": "after_close"},
                   {"signal_minutes": 5, "trades_per_session": "after_close"}],
}


def candidate():
    return replace(load_config(CANDIDATE), initial_equity=100000.0, risk_percent=0.5)


def ftmo_spread_ratio(ftmo_m1) -> float:
    """FTMO's median recorded US100 spread as a fraction of price, 09:30-12:00 NY, 2021-10 to 2024."""
    local = ftmo_m1.index.tz_convert(NY)
    minutes = local.hour * 60 + local.minute
    keep = (minutes >= 570) & (minutes < 720) & (local < pd.Timestamp("2025-01-01", tz=NY)) & (ftmo_m1.spread > 0)
    return float((ftmo_m1.spread[keep] * candidate().point / ftmo_m1.close[keep]).median())


def histdata(start: str, end: str, ratio: float):
    """HistData bars in [start, end) with the plan's cost model, and the config to trade them."""
    source = json.loads((HISTDATA / "HistData" / "NSXUSD" / "source.json").read_text(encoding="utf-8"))
    config = replace(candidate(), cache_dir=str(HISTDATA), expected_server="HistData", symbol="NSXUSD",
                     broker_clock="stored_as_utc", start=start, tick_start=start, development_end=end,
                     holdout_start=max(end, "2025-01-01"), holdout_opened=None, point=source["point"])
    m1, _ = load_m1(config)
    m1 = m1.loc[(m1.index >= pd.Timestamp(start, tz="UTC")) & (m1.index < pd.Timestamp(end, tz="UTC"))]
    spread_points = np.maximum(1, np.round(ratio * m1.close.to_numpy() / config.point)).astype(int)
    median_spread = float(np.median(spread_points)) * config.point
    config = replace(config, fallback_spread_price=median_spread, slippage_price=0.1 * median_spread)
    return m1.assign(spread=spread_points), config


def stats(r: pd.Series) -> dict:
    n = len(r)
    sd = float(r.std(ddof=1)) if n > 1 else float("nan")
    return {"trades": n, "mean_r": float(r.mean()) if n else None,
            "t": float(r.mean() / sd * math.sqrt(n)) if n > 1 and sd > 0 else None}


def calibrate() -> int:
    base = candidate()
    ftmo, _ = load_m1(base)
    ratio = ftmo_spread_ratio(ftmo)
    hist, hist_config = histdata("2021-10-01", "2025-01-01", ratio)
    # C1: 1-minute return correlation by lag, 09:30-16:00 New York
    a = ftmo.close.loc[ftmo.index < pd.Timestamp("2025-01-01", tz="UTC")]
    local = a.index.tz_convert(NY)
    minutes = local.hour * 60 + local.minute
    ra = a.pct_change()[(minutes >= 570) & (minutes < 960)]
    rb = hist.close.pct_change()
    lags = {lag: float(ra.corr(rb.shift(lag, freq="min").reindex(ra.index))) for lag in range(-90, 91)}
    best = max(lags, key=lambda k: lags[k])
    c1 = {"best_lag_min": best, "corr_at_0": lags[0], "corr_at_best": lags[best],
          "corr_at_+-60": [lags[-60], lags[60]], "pass": best == 0 and lags[0] >= 0.8}
    # C2: the frozen candidate on both feeds
    ft = backtest(ftmo, base)[0]
    ft = ft[pd.to_datetime(ft.timestamp, utc=True) < pd.Timestamp("2025-01-01", tz="UTC")]
    ht = backtest(hist, hist_config)[0]
    key = lambda t: t.session_date.astype(str) + "/" + t.direction  # noqa: E731
    matched = ft.assign(k=key(ft)).merge(ht.assign(k=key(ht)), on="k", suffixes=("_ftmo", "_hist"))
    c2 = {"ftmo": stats(ft.R_result), "histdata": stats(ht.R_result),
          "share_of_ftmo_trades_matched": len(matched) / len(ft),
          "r_correlation_matched": float(matched.R_result_ftmo.corr(matched.R_result_hist)),
          "pass": len(matched) / len(ft) >= 0.8}
    result = {"spread_ratio": ratio, "histdata_median_spread": hist_config.fallback_spread_price,
              "C1": c1, "C2": c2, "pass": c1["pass"] and c2["pass"]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "calibration.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def develop() -> int:
    calibration = json.loads((OUT / "calibration.json").read_text(encoding="utf-8"))
    if not calibration["pass"]:
        raise SystemExit("calibration failed: by the plan the study stops")
    base = candidate()
    m1, _ = load_m1(base)
    baseline = stats(backtest(m1, base)[0].R_result)
    rows, finalists = [], {}
    for family, settings in FAMILIES.items():
        best = None
        for overrides in settings:
            s = stats(backtest(m1, replace(base, **overrides))[0].R_result)
            eligible = family.startswith("D3") or s["trades"] >= 0.6 * baseline["trades"]
            rows.append({"family": family, "overrides": overrides, **s, "eligible": eligible})
            print(f"{family:22s} {json.dumps(overrides):55s} {s['mean_r']:+.4f} t={s['t']:+.2f} ({s['trades']}) "
                  f"{'' if eligible else 'below 60% of trades'}", flush=True)
            if eligible and (best is None or s["mean_r"] > best[1]["mean_r"]):
                best = (overrides, s)
        if best:
            finalists[family] = best[0]
    result = {"baseline": baseline, "runs": rows, "finalists": finalists}
    (OUT / "development.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("baseline", baseline, "\nfinalists", finalists)
    return 0


def months_to_funded(trades: pd.DataFrame, rng: np.random.Generator) -> float:
    """FTMO 1-Step at 1% risk, retries included: median months (as in the budgeting study)."""
    day = pd.to_datetime(trades.exit_time, utc=True).dt.tz_convert("Europe/Prague").dt.date
    pool = [g.to_numpy() for _, g in trades.R_result.groupby(day.values)]
    span = (pd.to_datetime(trades.exit_time, utc=True).max() - pd.to_datetime(trades.exit_time, utc=True).min()).days
    cal = span / len(pool)
    out = []
    for _ in range(2000):
        days = 0
        for _attempt in range(10):
            bal, hwm, profits, n, done = 1.0, 1.0, [], 0, None
            while done is None and n < 2500:
                rs = pool[rng.integers(0, len(pool))]
                n += 1
                start = bal
                hwm = max(hwm, start)
                for x in rs:
                    bal *= 1 + (1.0 if bal > 0.95 else 0.25) / 100 * x
                    if bal <= start * 0.97 or bal <= hwm - 0.10:
                        done = False
                        break
                if done is None:
                    profits.append(bal - start)
                    if bal >= 1.10 and max(profits) <= 0.5 * sum(p for p in profits if p > 0):
                        done = True
            days += n
            if done:
                break
        out.append(days * cal / 30.4)
    return float(np.median(out))


def holdout() -> int:
    target = OUT / "holdout.json"
    if target.exists():
        raise SystemExit(f"the holdout has already been run once: {target}")
    development = json.loads((OUT / "development.json").read_text(encoding="utf-8"))
    calibration = json.loads((OUT / "calibration.json").read_text(encoding="utf-8"))
    hist, config = histdata("2010-01-01", "2021-10-01", calibration["spread_ratio"])
    rng = np.random.default_rng(21)
    results = {}
    for name, overrides in {"H0 frozen candidate": {}, **development["finalists"]}.items():
        trades = backtest(hist, replace(config, **overrides))[0]
        stamp = pd.to_datetime(trades.timestamp, utc=True).dt.tz_convert(NY)
        yearly = trades.R_result.groupby(stamp.dt.year).mean()
        months = (stamp.max() - stamp.min()).days / 30.4
        results[name] = {"overrides": overrides, **stats(trades.R_result),
                         "years_positive_2010_2020": int((yearly.loc[2010:2020] > 0).sum()),
                         "yearly_mean_r": {int(y): float(v) for y, v in yearly.items()},
                         "trades_per_month": len(trades) / months,
                         "one_step_1pct_median_months": months_to_funded(trades, rng)}
        r = results[name]
        print(f"{name:22s} {r['mean_r']:+.4f}R t={r['t']:+.2f} ({r['trades']})  years+ {r['years_positive_2010_2020']}/11  "
              f"{r['trades_per_month']:.1f}/month  1-Step ~{r['one_step_1pct_median_months']:.1f} months", flush=True)
    h0 = results["H0 frozen candidate"]
    h0["pass"] = bool(h0["mean_r"] > 0 and h0["t"] >= 2.0 and h0["years_positive_2010_2020"] >= 7)
    for name, r in results.items():
        if name != "H0 frozen candidate":
            r["pass"] = bool(r["mean_r"] > 0 and r["t"] >= 2.5 and r["mean_r"] > h0["mean_r"])
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v["pass"] for k, v in results.items()}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("calibrate", "develop", "holdout"))
    step = parser.parse_args(argv).step
    return {"calibrate": calibrate, "develop": develop, "holdout": holdout}[step]()


if __name__ == "__main__":
    raise SystemExit(main())
