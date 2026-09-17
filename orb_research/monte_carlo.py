"""Fixed-risk trade-order Monte Carlo; diagnostics, never a forecast guarantee."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _one_method(r: np.ndarray, monthly_counts: np.ndarray, risk_fraction: float,
                paths: int, rng: np.random.Generator, replacement: bool) -> pd.DataFrame:
    columns = ["max_drawdown_pct", "ending_return_pct", "longest_losing_streak", "worst_month_pct"]
    out = []
    boundaries = np.cumsum(monthly_counts)
    for batch_start in range(0, paths, 1000):
        count = min(1000, paths - batch_start)
        if replacement:
            sampled = r[rng.integers(0, len(r), size=(count, len(r)))]
        else:
            sampled = np.stack([rng.permutation(r) for _ in range(count)])
        factor = 1 + risk_fraction * sampled
        if (factor <= 0).any():
            raise ValueError("a Monte Carlo trade would deplete the simulated account")
        equity = np.concatenate([np.ones((count, 1)), np.cumprod(factor, axis=1)], axis=1)
        peak = np.maximum.accumulate(equity, axis=1)
        max_dd = (1 - equity / peak).max(axis=1) * 100
        ending = (equity[:, -1] - 1) * 100
        running = np.zeros(count, dtype=int)
        longest = np.zeros(count, dtype=int)
        for index in range(sampled.shape[1]):
            running = np.where(sampled[:, index] < 0, running + 1, 0)
            longest = np.maximum(longest, running)
        starts = np.concatenate([[0], boundaries[:-1]])
        monthly = np.stack([equity[:, stop] / equity[:, start] - 1
                            for start, stop in zip(starts, boundaries)], axis=1)
        worst = monthly.min(axis=1) * 100
        out.append(pd.DataFrame(dict(zip(columns, (max_dd, ending, longest, worst)))))
    return pd.concat(out, ignore_index=True)


def trade_sequence_monte_carlo(trades: pd.DataFrame, risk_percent: float,
                               paths: int = 10_000, seed: int = 20260917):
    if paths < 10_000:
        raise ValueError("at least 10,000 paths per method are required")
    if trades.empty or len(trades) < 2:
        raise ValueError("at least two trades are required")
    ordered = trades.sort_values("exit_time")
    r = ordered.R_result.to_numpy(dtype=float)
    if not np.isfinite(r).all():
        raise ValueError("trade R results must be finite")
    months = ordered.exit_time.dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period("M")
    counts = months.value_counts().sort_index().to_numpy(dtype=int)
    rng = np.random.default_rng(seed)
    draws = []
    for method, replacement in (("reshuffle", False), ("bootstrap", True)):
        result = _one_method(r, counts, risk_percent / 100, paths, rng, replacement)
        result.insert(0, "method", method)
        result.insert(1, "path", np.arange(paths))
        draws.append(result)
    paths_frame = pd.concat(draws, ignore_index=True)
    summary = (paths_frame.groupby("method")
               [["max_drawdown_pct", "ending_return_pct", "longest_losing_streak", "worst_month_pct"]]
               .quantile([0.5, 0.75, 0.9, 0.95, 0.99])
               .rename_axis(index=["method", "percentile"]).reset_index())
    return summary, paths_frame
