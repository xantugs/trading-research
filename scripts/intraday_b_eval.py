"""Idea B evaluation (docs/INTRADAY_CHARTS_PLAN.md).

    .venv/Scripts/python -m scripts.intraday_b_eval gate-b1          # integrity, 2012-2017
    .venv/Scripts/python -m scripts.intraday_b_eval baselines        # baselines on 2012-2017 only

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

DATA = Path("C:/data/intraday_b")
COST = 0.0008
BASELINES = ("mom", "rev", "s2", "wrev", "hks")


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
        meta = load_meta(key, range(2012, 2018))
        for s in BASELINES:
            rows += [dict(r, T=key) for r in report(meta, s, s)]
    out = pd.DataFrame(rows)[["T", "score", "weights", "days", "gross_bps", "gross_sharpe", "gross_t", "net_sharpe", "net_t"]]
    Path("outputs/intraday_b").mkdir(parents=True, exist_ok=True)
    out.to_csv("outputs/intraday_b/baselines_dev_2012_2017.csv", index=False)
    pd.set_option("display.width", 200)
    print(out.round(2).to_string(index=False))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=("gate-b1", "baselines"))
    a = p.parse_args()
    gate_b1() if a.what == "gate-b1" else baselines()


if __name__ == "__main__":
    main()
