#!/usr/bin/env python3
"""Follow-up 2 of docs/ML_PLAN.md: the walk-forward on the Nasdaq-100 and the S&P 500 with the
blend, a logistic-regression baseline and an S2-signal rule, plus AUC overfitting gaps and
XGBoost feature importance.

    python -m scripts.ml_diagnostics
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from orb_research.ml_intraday import FEATURES, trade_direct
from orb_research.ml_models import Blend, Standardiser
from scripts.ml_walkforward import GAP_DAYS, arrays
from scripts.s2_basket_study import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/ml"


def split_before(dec: pd.DataFrame, end: pd.Timestamp):
    dates = pd.to_datetime(dec.date)
    days = np.sort(dates[dates < end].unique())
    cutoff = days[-GAP_DAYS]
    window = dates < cutoff
    inner = window & (dates >= cutoff - pd.DateOffset(months=12))
    return (window & ~inner).to_numpy(), inner.to_numpy()


class LogReg:
    def fit(self, x, y, w):
        self.scaler = Standardiser().fit(x)
        z = self.scaler.transform(x)
        self.models = [LogisticRegression(C=1.0, max_iter=2000).fit(z, y[:, j], sample_weight=w) for j in range(y.shape[1])]
        return self

    def predict(self, x):
        z = self.scaler.transform(x)
        return np.mean([m.predict_proba(z)[:, 1] for m in self.models], axis=0)


def auc(p, y) -> float | None:
    ok = np.isfinite(p)
    return float(roc_auc_score(y[ok], p[ok])) if len(np.unique(y[ok])) == 2 else None


def run_market(name: str, hist: dict, ftmo: dict) -> dict:
    dec = hist[name]["decisions"]
    close, spread, _ = hist["grids"][name]
    f_dec = ftmo[name]["decisions"]
    f_dec = f_dec[pd.to_datetime(f_dec.date) >= pd.Timestamp("2025-01-01")]
    f_close, f_spread, _ = ftmo["grids"][name]
    x, y, w, ok = arrays(dec)
    dates = pd.to_datetime(dec.date)
    periods = [(str(y_), (dates.dt.year == y_).to_numpy(), dec, close, spread, pd.Timestamp(f"{y_}-01-01"))
               for y_ in range(2012, 2025)]
    periods.append(("FTMO 2025-26", None, f_dec, f_close, f_spread, pd.Timestamp("2025-01-01")))
    daily = {"blend": [], "logreg": [], "s2_rule": []}
    rows = {}
    final_blend = None
    for label, mask, rows_dec, g_close, g_spread, end in periods:
        train, valid = split_before(dec, end)
        train &= ok
        valid &= ok
        blend = Blend().fit(x[train], y[train], w[train], x[valid], y[valid], w[valid])
        logreg = LogReg().fit(x[train | valid], y[train | valid], w[train | valid])
        part = rows_dec[mask] if mask is not None else rows_dec
        xp = part[list(FEATURES)].to_numpy(np.float64)
        probs = {"blend": blend.predict(xp), "logreg": logreg.predict(xp),
                 "s2_rule": 0.5 + 0.5 * part.s2_signal.to_numpy(np.float64)}
        entry = {}
        for model, p in probs.items():
            d = trade_direct(part, p, 0.0, g_close, g_spread)
            daily[model].append(d)
            entry[model] = round(stats(d.ret, d.trades)["mean_bps"], 2)
        y_oos = (part.fwd30.to_numpy() > 0).astype(int)
        entry["blend_auc_train"] = auc(blend.predict(x[train]), y[train, 0].astype(int))
        entry["blend_auc_oos"] = auc(probs["blend"], y_oos)
        rows[label] = entry
        final_blend = blend
        print(f"{name} {label}: blend {entry['blend']:+.2f} logreg {entry['logreg']:+.2f} s2_rule {entry['s2_rule']:+.2f} "
              f"bps/day | AUC train {entry['blend_auc_train']:.3f} oos {entry['blend_auc_oos']:.3f}", flush=True)
    pooled = {}
    for model, parts in daily.items():
        joined = pd.concat(parts)
        s = stats(joined.ret, joined.trades)
        pooled[model] = {"mean_bps": s["mean_bps"], "t": s["t"], "sharpe": s["sharpe"],
                         "win_days": float((joined.ret > 0).mean()),
                         "positive_periods": sum(r[model] > 0 for r in rows.values())}
    gains = {}
    for booster in final_blend.xgb:
        for key, value in booster.get_score(importance_type="gain").items():
            gains[FEATURES[int(key[1:])]] = gains.get(FEATURES[int(key[1:])], 0.0) + value / len(final_blend.xgb)
    importance = dict(sorted(gains.items(), key=lambda kv: -kv[1]))
    total = sum(importance.values())
    importance = {k: round(v / total, 4) for k, v in importance.items()}
    blend = pooled["blend"]
    consistent = (blend["t"] or 0) >= 2.0 and blend["positive_periods"] >= 0.6 * len(rows)
    return {"periods": rows, "pooled": pooled, "importance_share": importance,
            "verdict": "consistent" if consistent else "luck (not consistent)"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="", help="suffix for the results file (a separate, labelled run)")
    target = OUT / f"diagnostics{parser.parse_args(argv).tag}.json"
    if target.exists():
        raise SystemExit(f"already run: {target}")
    with open(OUT / "data_hist.pkl", "rb") as handle:
        hist = pickle.load(handle)
    with open(OUT / "data_ftmo.pkl", "rb") as handle:
        ftmo = pickle.load(handle)
    results = {name: run_market(name, hist, ftmo) for name in ("NSX", "SPX")}
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for name, r in results.items():
        for model, p in r["pooled"].items():
            print(f"POOLED {name} {model:8s}: {p['mean_bps']:+.2f} bps/day t={p['t']:+.2f} Sharpe {p['sharpe']:+.2f} "
                  f"win days {p['win_days']:.0%} positive {p['positive_periods']}/14")
        print(f"{name} verdict: {r['verdict']}; top features: {list(r['importance_share'].items())[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
