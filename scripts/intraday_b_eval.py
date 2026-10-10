"""Idea B evaluation (docs/INTRADAY_CHARTS_PLAN.md).

    .venv/Scripts/python -m scripts.intraday_b_eval gate-b1          # integrity, 2012-2017
    .venv/Scripts/python -m scripts.intraday_b_eval baselines        # baselines on 2012-2017 only
    .venv/Scripts/python -m scripts.intraday_b_eval cnn --key 1200 --part dev    # CNN on 2012-2017 (in-sample)
    .venv/Scripts/python -m scripts.intraday_b_eval cnn --key 1200 --part test   # 2018-2020, runs ONCE per key

Portfolios: each day, deciles on the score; H-L = decile 10 - decile 1, held from the first trade
after T to the CRSP close. Net = gross - 8 bps a day (2 bps per side per leg). Sharpe by sqrt(252);
Newey-West t with 5 lags.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research import crsp
from orb_research.jkx_portfolio import decile_returns
from orb_research.taq_bars import load_bars
from orb_research.vault import n_runs, record_run

DATA = Path("C:/data/intraday_b")
COST = 0.0008
BASELINES = ("mom", "rev", "s2", "wrev", "hks")
LR_FEATURES = ("mom", "rev", "s2", "wrev", "hks", "rv")


def add_logit(meta: pd.DataFrame) -> pd.DataFrame:
    """Baseline 6: logistic regression of the label on the five baseline scores plus realised
    volatility so far, fitted on 2012-2016 only (standardised on 2012-2016, NaN -> 0)."""
    from sklearn.linear_model import LogisticRegression

    x = meta[list(LR_FEATURES)]
    fit_rows = (meta["date"].dt.year <= 2016) & (meta["label"] >= 0)
    mu, sd = x[fit_rows].mean(), x[fit_rows].std().replace(0, 1)
    z = ((x - mu) / sd).clip(-10, 10).fillna(0.0).to_numpy()
    model = LogisticRegression(max_iter=1000).fit(z[fit_rows.to_numpy()], meta.loc[fit_rows, "label"])
    return meta.assign(logit=model.predict_proba(z)[:, 1])


def load_meta(key: str, years) -> pd.DataFrame:
    return pd.concat([pd.read_parquet(DATA / f"T{key}" / f"{y}_meta.parquet") for y in years], ignore_index=True)


def stats(r: pd.Series) -> dict:
    r = r.dropna().to_numpy()
    if len(r) < 20:
        return {"n": len(r)}
    m, sd = r.mean(), r.std(ddof=1)
    x = r - m
    lags = 5
    g = [x @ x / len(x)] + [x[k:] @ x[:-k] / len(x) for k in range(1, lags + 1)]
    nw = g[0] + 2 * sum((1 - k / (lags + 1)) * g[k] for k in range(1, lags + 1))
    return {"n": len(r), "mean_bps": m * 1e4, "sharpe": m / sd * np.sqrt(252), "t_nw": m / np.sqrt(nw / len(r))}


def hl(meta: pd.DataFrame, score: str, weight: str | None = None, top: int | None = None) -> pd.DataFrame:
    f = meta[meta["label"] >= 0]
    table = decile_returns(f, score, ret="ret", weight=weight, top=top)
    return table


def report(meta: pd.DataFrame, score: str, label: str) -> list[dict]:
    rows = []
    for scheme, w, top in (("ew", None, None), ("vw", "mcap", None), ("vw_top100", "mcap", 100)):
        t = hl(meta, score, w, top)
        g, n = stats(t["H-L"]), stats(t["H-L"] - COST)
        rows.append({"score": label, "weights": scheme, "days": g.get("n"), "gross_bps": g.get("mean_bps"),
                     "gross_sharpe": g.get("sharpe"), "gross_t": g.get("t_nw"),
                     "net_sharpe": n.get("sharpe"), "net_t": n.get("t_nw")})
    return rows


def gate_b1() -> None:
    years = range(2012, 2018)
    # 1. entry prices: the day's first bar open against the CRSP open
    shares = []
    for y in years:
        b = load_bars(f"{y}-01-01", f"{y}-12-31", columns=["date", "permno", "b", "open", "high", "low"])
        first = b.sort_values("b").groupby(["date", "permno"]).head(1)
        c = crsp.load_daily(f"{y}-01-01", f"{y}-12-31")[["date", "permno", "openprc"]]
        m = first.merge(c, on=["date", "permno"])
        o = m["openprc"].abs()
        # amendment 1: the official open lies inside the first bar's range (+-0.1%) or within 0.5% of its open
        ok = np.isclose(m["open"], o, rtol=0.005) | ((o >= m["low"] * 0.999) & (o <= m["high"] * 1.001))
        shares.append(ok.mean())
        print(f"{y}: CRSP open inside the first bar (or within 0.5% of its open) on {ok.mean():.4f} "
              f"of {len(m):,} stock-days", flush=True)
    c1 = min(shares) >= 0.99
    meta = load_meta("1200", years)
    rng = np.random.default_rng(0)
    meta["random"] = rng.normal(size=len(meta))
    rt = stats(hl(meta, "random")["H-L"])
    c2 = abs(rt["t_nw"]) < 3
    pf = hl(meta, "ret")
    means = pf[list(range(1, 11))].mean()
    c3 = bool((np.diff(means.to_numpy()) > 0).all())
    print(f"\n1. entry prices: worst year {min(shares):.4f} (>= 0.99) -> {'pass' if c1 else 'FAIL'}")
    print(f"2. random score H-L gross t = {rt['t_nw']:.2f} (|t| < 3) -> {'pass' if c2 else 'FAIL'}")
    print(f"3. perfect foresight deciles (bps/day): {' '.join(f'{v * 1e4:.0f}' for v in means)} -> "
          f"{'pass' if c3 else 'FAIL'}")
    print("\nGATE B1:", "PASS" if c1 and c2 and c3 else "FAIL")


def baselines() -> None:
    rows = []
    for key in ("1030", "1200", "1400"):
        meta = add_logit(load_meta(key, range(2012, 2018)))
        for s in BASELINES:
            rows += [dict(r, T=key) for r in report(meta, s, s)]
        # the logistic regression is fitted on 2012-2016, so its fair dev check is 2017 only
        rows += [dict(r, T=key) for r in report(meta[meta["date"].dt.year == 2017], "logit", "logit (2017 only)")]
    out = pd.DataFrame(rows)[["T", "score", "weights", "days", "gross_bps", "gross_sharpe", "gross_t", "net_sharpe", "net_t"]]
    Path("outputs/intraday_b").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/intraday_b/baselines_dev_2012_2017.csv", index=False)
    pd.set_option("display.width", 200)
    print(out.round(2).to_string(index=False))


def cnn(key: str, part: str) -> None:
    """The frozen ensemble's predictions (scripts.intraday_b_train --stage predict) through the same
    portfolios as the baselines. `dev` (2012-2017) is in-sample for 2012-2016 and the early-stopping
    year for 2017, so it is a sanity check only. `test` (2018-2020) is the pre-registered evaluation:
    it runs once per decision time (the ledger refuses a second run) and prints the pass condition."""
    experiment = f"intraday_b/{part}_T{key}"
    if part == "test" and n_runs(experiment) > 0:
        raise SystemExit(f"{experiment} has already been evaluated (outputs/ledger.jsonl); the plan allows one run")
    pred = pd.read_parquet(DATA / "predictions" / f"T{key}_{part}.parquet")
    years = sorted(pred["date"].dt.year.unique())
    rows = [dict(r, years="all") for r in report(pred, "prob", "cnn")]
    for y in years:
        rows += [dict(r, years=str(y)) for r in report(pred[pred["date"].dt.year == y], "prob", "cnn")]
    best = None
    if part == "test":
        # baselines over the same test days; the logistic regression keeps its 2012-2016 fit
        meta = add_logit(pd.concat([load_meta(key, range(2012, 2018)), pred.drop(columns=[c for c in pred if c.startswith("prob")])],
                                   ignore_index=True))
        meta = meta[meta["date"].dt.year >= years[0]]
        for s in (*BASELINES, "logit"):
            rows += [dict(r, years="all") for r in report(meta, s, s)]
    out = pd.DataFrame(rows)[["years", "score", "weights", "days", "gross_bps", "gross_sharpe", "gross_t", "net_sharpe", "net_t"]]
    Path("outputs/intraday_b").mkdir(parents=True, exist_ok=True)
    out.to_csv(f"outputs/intraday_b/cnn_{part}_T{key}.csv", index=False)
    pd.set_option("display.width", 200)
    print(out.round(2).to_string(index=False))
    metrics = {"ew_net_t": None, "vw_top100_net_sharpe": None}
    if part == "test":
        all_ = out[out["years"] == "all"].set_index(["score", "weights"])
        ew_t = all_.loc[("cnn", "ew"), "net_t"]
        vw_s = all_.loc[("cnn", "vw_top100"), "net_sharpe"]
        ew_s = all_.loc[("cnn", "ew"), "net_sharpe"]
        base = all_.xs("ew", level="weights")["net_sharpe"].drop("cnn")
        best = base.idxmax()
        c1, c2, c3 = ew_t >= 3.0, vw_s > 0, ew_s - base[best] >= 0.3
        print(f"\n1. equal-weight H-L net NW t = {ew_t:.2f} (>= 3.0) -> {'pass' if c1 else 'FAIL'}")
        print(f"2. top-100 value-weight H-L net Sharpe = {vw_s:.2f} (> 0) -> {'pass' if c2 else 'FAIL'}")
        print(f"3. CNN equal-weight net Sharpe {ew_s:.2f} vs best baseline {best} {base[best]:.2f} "
              f"(margin >= 0.3) -> {'pass' if c3 else 'FAIL'}")
        verdict = "PASS" if c1 and c2 and c3 else "FAIL"
        print(f"\nT{key} test 2018-2020: {verdict}" + ("" if key == "1200" else " (reported, not gated)"))
        metrics = {"ew_net_t": float(ew_t), "vw_top100_net_sharpe": float(vw_s), "ew_net_sharpe": float(ew_s),
                   "best_baseline": best, "best_baseline_ew_net_sharpe": float(base[best]), "verdict": verdict}
    record_run(experiment, {"key": key, "part": part, "years": [int(y) for y in years]}, metrics)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=("gate-b1", "baselines", "cnn"))
    p.add_argument("--key", choices=("1030", "1200", "1400"))
    p.add_argument("--part", choices=("dev", "test"))
    a = p.parse_args()
    if a.what == "cnn":
        if not (a.key and a.part):
            p.error("cnn needs --key and --part")
        cnn(a.key, a.part)
    elif a.what == "gate-b1":
        gate_b1()
    else:
        baselines()


if __name__ == "__main__":
    main()
