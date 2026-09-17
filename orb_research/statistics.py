"""Descriptive confidence intervals with explicit time-dependence caveats."""
from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd


def confidence_intervals(trades: pd.DataFrame, paths: int = 10_000,
                         seed: int = 20260917) -> pd.DataFrame:
    if trades.empty or paths < 10_000:
        raise ValueError("nonempty trades and at least 10,000 bootstrap paths are required")
    ordered = trades.sort_values("exit_time")
    r = ordered.R_result.to_numpy(dtype=float)
    wins = (ordered.PnL.to_numpy(dtype=float) > 0).astype(float)
    if not np.isfinite(r).all():
        raise ValueError("non-finite R results")
    rng = np.random.default_rng(seed)
    means, win_rates = [], []
    for count in range(0, paths, 1000):
        batch = min(1000, paths - count)
        selection = rng.integers(0, len(r), size=(batch, len(r)))
        means.append(r[selection].mean(axis=1))
        win_rates.append(wins[selection].mean(axis=1))
    iid_mean = np.concatenate(means)
    iid_win = np.concatenate(win_rates)
    rows = [
        ("mean_r_trade_bootstrap", float(r.mean()), *np.quantile(iid_mean, [0.025, 0.975])),
        ("win_probability_trade_bootstrap", float(wins.mean()), *np.quantile(iid_win, [0.025, 0.975])),
    ]
    n = len(r)
    z = 1.959963984540054
    p = float(wins.mean())
    midpoint = (p + z * z / (2 * n)) / (1 + z * z / n)
    radius = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    rows.append(("win_probability_wilson", p, midpoint - radius, midpoint + radius))
    months = ordered.exit_time.dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("M")
    blocks = [ordered.loc[months == month, "R_result"].to_numpy(dtype=float)
              for month in months.unique()]
    if len(blocks) >= 3:
        block_means = np.empty(paths)
        for index in range(paths):
            sample = rng.integers(0, len(blocks), size=len(blocks))
            total = sum(blocks[i].sum() for i in sample)
            size = sum(len(blocks[i]) for i in sample)
            block_means[index] = total / size
        rows.append(("mean_r_month_block_bootstrap", float(r.mean()),
                     *np.quantile(block_means, [0.025, 0.975])))
    return pd.DataFrame(rows, columns=["metric", "estimate", "lower_95", "upper_95"])
