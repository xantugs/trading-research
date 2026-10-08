"""Decile portfolios on a score, as in Jiang, Kelly & Xiu (2023), Tables I, II and IV.

Every formation date, stocks are sorted into deciles on the score (decile 10 = highest
predicted up probability). Equal- and value-weight (market cap at formation) returns are
held to the next formation date; H-L is decile 10 minus decile 1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = {"week": 52, "month": 12, "quarter": 4}

# H-L Sharpe ratios reported in the paper (test sample 2001-2019), the replication targets.
PAPER_HL_SHARPE = {
    # Table I: weekly rebalancing
    ("I5/R5", "ew"): 7.15, ("I20/R5", "ew"): 6.75, ("I60/R5", "ew"): 4.89,
    ("MOM/R5", "ew"): 0.07, ("STR/R5", "ew"): 1.76, ("WSTR/R5", "ew"): 2.84,
    ("I5/R5", "vw"): 1.49, ("I20/R5", "vw"): 1.74, ("I60/R5", "vw"): 1.44,
    ("MOM/R5", "vw"): 0.33, ("STR/R5", "vw"): 0.44, ("WSTR/R5", "vw"): 0.77,
    # Table II: monthly and quarterly rebalancing, equal weight
    ("I5/R20", "ew"): 2.35, ("I20/R20", "ew"): 2.16, ("I60/R20", "ew"): 1.29,
    ("MOM/R20", "ew"): 0.25, ("STR/R20", "ew"): 0.55, ("WSTR/R20", "ew"): 1.23,
    ("I5/R60", "ew"): 1.30, ("I20/R60", "ew"): 0.37, ("I60/R60", "ew"): 0.43,
    ("MOM/R60", "ew"): 0.06, ("STR/R60", "ew"): 0.34, ("WSTR/R60", "ew"): 0.65,
    # Table IV: the 500 largest stocks, weekly
    ("I5/R5", "ew500"): 1.02, ("I20/R5", "ew500"): 0.78, ("I60/R5", "ew500"): 1.08,
    ("I5/R5", "vw500"): 1.29, ("I20/R5", "vw500"): 0.96, ("I60/R5", "vw500"): 1.03,
}


def assign_deciles(frame: pd.DataFrame, score: str) -> pd.Series:
    """Decile 1..10 of `score` within each date (ties broken by order, NaN scores dropped)."""
    def one(s: pd.Series) -> pd.Series:
        s = s.dropna()
        if len(s) < 10:
            return pd.Series(np.nan, index=s.index)
        return pd.Series(pd.qcut(s.rank(method="first"), 10, labels=False) + 1, index=s.index)
    return frame.groupby("date", group_keys=False)[score].apply(one).reindex(frame.index)


def decile_returns(frame: pd.DataFrame, score: str, ret: str = "hold_ret", weight: str | None = None,
                   top: int | None = None) -> pd.DataFrame:
    """(date x decile 1..10, plus "H-L") holding-period returns.

    `weight` = None for equal weight or a market-cap column; `top` keeps only the largest
    `top` stocks by market cap at each date before sorting."""
    f = frame.dropna(subset=[score, ret])
    if top is not None:
        f = f[f.groupby("date")["mcap"].rank(ascending=False, method="first") <= top]
    f = f.assign(decile=assign_deciles(f, score)).dropna(subset=["decile"])
    if weight is None:
        table = f.groupby(["date", "decile"])[ret].mean().unstack()
    else:
        w = f[weight].clip(lower=0)
        num = (f[ret] * w).groupby([f["date"], f["decile"]]).sum()
        den = w.groupby([f["date"], f["decile"]]).sum()
        table = (num / den).unstack()
    table.columns = [int(c) for c in table.columns]
    table["H-L"] = table[10] - table[1]
    return table


def summarize(returns: pd.Series, freq: str) -> dict:
    """Annualised mean and Sharpe ratio, plain and Newey-West (4 lags) t-statistics."""
    r = returns.dropna().to_numpy()
    ppy = PERIODS_PER_YEAR[freq]
    mean, sd = r.mean(), r.std(ddof=1)
    x = r - mean
    lags = 4
    gamma = [x @ x / len(x)] + [x[k:] @ x[:-k] / len(x) for k in range(1, lags + 1)]
    nw_var = gamma[0] + 2 * sum((1 - k / (lags + 1)) * gamma[k] for k in range(1, lags + 1))
    return {"periods": len(r), "ann_ret": mean * ppy, "sharpe": mean / sd * np.sqrt(ppy),
            "t": mean / (sd / np.sqrt(len(r))), "t_nw": mean / np.sqrt(nw_var / len(r))}


def hl_turnover(frame: pd.DataFrame, score: str, freq: str, ret: str = "hold_ret", weight: str | None = None) -> float:
    """Monthly turnover of the H-L portfolio, the paper's formula: the average sum of
    |w(t+1) - w(t)(1 + r)/(1 + sum w r)| per rebalance, divided by months per holding period.
    Each leg has gross weight 0.5, so replacing every holding counts as 200% (the paper's
    maximum)."""
    f = frame.dropna(subset=[score, ret])
    f = f.assign(decile=assign_deciles(f, score)).dropna(subset=["decile"])
    f = f[f["decile"].isin([1, 10])]
    raw = np.ones(len(f)) if weight is None else f[weight].clip(lower=0).to_numpy()
    sign = np.where(f["decile"] == 10, 1.0, -1.0)
    leg_total = pd.Series(raw).groupby([f["date"].to_numpy(), sign]).transform("sum").to_numpy()
    f = f.assign(w=0.5 * sign * raw / leg_total)     # gross weight 1, so a full turnover is 200%
    dates = np.sort(f["date"].unique())
    by_date = {d: g.set_index("permno") for d, g in f.groupby("date")}
    total = []
    for d0, d1 in zip(dates[:-1], dates[1:]):
        a, b = by_date[d0], by_date[d1]
        grown = a["w"] * (1 + a[ret]) / (1 + (a["w"] * a[ret]).sum())
        both = grown.index.union(b.index)
        total.append(np.abs(b["w"].reindex(both, fill_value=0) - grown.reindex(both, fill_value=0)).sum())
    months = {"week": 12 / 52, "month": 1.0, "quarter": 3.0}[freq]
    return float(np.mean(total) / months) if total else float("nan")
