#!/usr/bin/env python3
"""Walk-forward luck check for Project A (docs/ML_PLAN.md, follow-up): every year is traded
by a model retrained only on earlier years, with the recipe unchanged and theta = 0.

    python -m scripts.ml_walkforward
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.ml_intraday import FEATURES, trade_direct
from orb_research.ml_models import Blend
from scripts.s2_basket_study import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/ml"
GAP_DAYS = 5


def arrays(dec: pd.DataFrame):
    x = dec[list(FEATURES)].to_numpy(np.float64)
    y = np.stack([dec.fwd30 > 0, dec.fwd60 > 0, dec.fwd_close > 0], axis=1).astype(np.float32)
    w = np.abs(dec[["fwd30", "fwd60", "fwd_close"]].to_numpy()).mean(axis=1) + 1e-6
    ok = np.isfinite(dec[["fwd30", "fwd60", "fwd_close"]].to_numpy()).all(axis=1)
    return x, y, w, ok


def fit_before(dec: pd.DataFrame, end: pd.Timestamp) -> Blend:
    """Train on rows before `end` minus the gap; the last 12 months are the early-stopping set."""
    dates = pd.to_datetime(dec.date)
    days = np.sort(dates[dates < end].unique())
    cutoff = days[-GAP_DAYS] if len(days) > GAP_DAYS else end
    window = dates < cutoff
    inner = window & (dates >= cutoff - pd.DateOffset(months=12))
    x, y, w, ok = arrays(dec)
    train, valid = (window & ~inner).to_numpy() & ok, inner.to_numpy() & ok
    return Blend().fit(x[train], y[train], w[train], x[valid], y[valid], w[valid])


def main() -> int:
    target = OUT / "walkforward.json"
    if target.exists():
        raise SystemExit(f"the walk-forward check has already been run: {target}")
    with open(OUT / "data_hist.pkl", "rb") as handle:
        hist = pickle.load(handle)
    with open(OUT / "data_ftmo.pkl", "rb") as handle:
        ftmo = pickle.load(handle)
    dec = hist["NSX"]["decisions"]
    close, spread, _ = hist["grids"]["NSX"]
    dates = pd.to_datetime(dec.date)
    pieces, rows = [], {}
    for year in range(2012, 2025):
        model = fit_before(dec, pd.Timestamp(f"{year}-01-01"))
        part = dec[(dates.dt.year == year).to_numpy()]
        daily = trade_direct(part, model.predict(part[list(FEATURES)].to_numpy(np.float64)), 0.0, close, spread)
        pieces.append(daily)
        rows[str(year)] = stats(daily.ret, daily.trades)
        print(f"{year}: {rows[str(year)]['mean_bps']:+.2f} bps/day t={rows[str(year)]['t']:+.2f} "
              f"win days {(daily.ret > 0).mean():.0%}", flush=True)
    model = fit_before(dec, pd.Timestamp("2025-01-01"))
    f_dec = ftmo["NSX"]["decisions"]
    f_dec = f_dec[pd.to_datetime(f_dec.date) >= pd.Timestamp("2025-01-01")]
    f_close, f_spread, _ = ftmo["grids"]["NSX"]
    daily = trade_direct(f_dec, model.predict(f_dec[list(FEATURES)].to_numpy(np.float64)), 0.0, f_close, f_spread)
    pieces.append(daily)
    rows["FTMO 2025-26"] = stats(daily.ret, daily.trades)
    print(f"FTMO 2025-26: {rows['FTMO 2025-26']['mean_bps']:+.2f} bps/day t={rows['FTMO 2025-26']['t']:+.2f} "
          f"win days {(daily.ret > 0).mean():.0%}", flush=True)
    pooled_daily = pd.concat(pieces)
    pooled = stats(pooled_daily.ret, pooled_daily.trades)
    positive = sum(r["mean_bps"] > 0 for r in rows.values())
    consistent = (pooled["t"] or 0) >= 2.0 and positive >= 0.6 * len(rows)
    result = {"periods": rows, "pooled": pooled, "positive_periods": positive, "n_periods": len(rows),
              "pooled_win_days": float((pooled_daily.ret > 0).mean()),
              "verdict": "consistent" if consistent else "luck (not consistent)"}
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"POOLED 2012-2026: {pooled['mean_bps']:+.2f} bps/day t={pooled['t']:+.2f} Sharpe {pooled['sharpe']:+.2f} "
          f"win days {result['pooled_win_days']:.0%}, positive periods {positive}/{len(rows)} -> {result['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
