"""Idea B samples use only what is known at the decision time (docs/INTRADAY_CHARTS_PLAN.md)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from orb_research.intraday_b import DECISIONS, build_samples, make_cube


def _market(n_days=25, n_stocks=80, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-01-02", periods=n_days)
    rows, daily = [], []
    for s in range(n_stocks):
        level = 50.0
        for d in dates:
            path = level * np.exp(np.cumsum(rng.normal(0, 0.002, 78)))
            opn = np.r_[level, path[:-1]]
            hi = np.maximum(opn, path) * (1 + rng.uniform(0, 0.001, 78))
            lo = np.minimum(opn, path) * (1 - rng.uniform(0, 0.001, 78))
            vol = rng.integers(1_000, 50_000, 78)
            rows.append(pd.DataFrame({"date": d, "permno": 10_000 + s, "b": np.arange(78), "open": opn, "high": hi,
                                      "low": lo, "close": path, "volume": vol, "vwap": (hi + lo) / 2}))
            close = path[-1] * (1 + rng.normal(0, 0.0005))          # closing auction
            daily.append({"date": d, "permno": 10_000 + s, "prc": close, "openprc": opn[0],
                          "ret": close / level - 1, "shrout": 1000.0 + s})
            level = close
    return pd.concat(rows, ignore_index=True), pd.DataFrame(daily)


def _samples(bars, daily, n_t, day):
    meta, packed = build_samples(make_cube(bars, daily), n_t, pd.DatetimeIndex([day]))
    return meta.reset_index(drop=True), packed


def test_nothing_after_the_decision_time_changes_images_or_scores() -> None:
    bars, daily = _market()
    day = np.sort(bars["date"].unique())[20]
    for n_t in DECISIONS.values():
        before_meta, before_img = _samples(bars, daily, n_t, day)
        assert len(before_meta) > 50
        b2, d2 = bars.copy(), daily.copy()
        rng = np.random.default_rng(7)
        future = (b2["date"] > day) | ((b2["date"] == day) & (b2["b"] >= n_t))
        for c in ("open", "high", "low", "close", "vwap"):
            b2.loc[future, c] *= rng.uniform(0.9, 1.1, future.sum())
        b2.loc[future, "volume"] = rng.integers(1, 10**6, future.sum())
        later = d2["date"] >= day
        d2.loc[later, "prc"] *= rng.uniform(0.9, 1.1, later.sum())
        d2.loc[later, "ret"] = rng.normal(0, 0.05, later.sum())
        after_meta, after_img = _samples(b2, d2, n_t, day)
        cols = ["permno", "mcap", "mom", "rev", "s2", "wrev", "hks", "rv"]
        pd.testing.assert_frame_equal(before_meta[cols], after_meta[cols])
        assert (before_img == after_img).all(), f"images changed at n_t={n_t}"
        assert not np.allclose(before_meta["ret"], after_meta["ret"])     # labels look ahead, by design


def test_entry_is_the_first_trade_after_the_decision() -> None:
    bars, daily = _market()
    day = np.sort(bars["date"].unique())[20]
    n_t = DECISIONS["1200"]
    meta, _ = _samples(bars, daily, n_t, day)
    p = int(meta["permno"].iloc[0])
    row_b = bars[(bars.date == day) & (bars.permno == p)].set_index("b")
    close = daily[(daily.date == day) & (daily.permno == p)]["prc"].iloc[0]
    assert np.isclose(meta["ret"].iloc[0], close / row_b.loc[n_t, "open"] - 1)
    # drop the bar right after T: entry moves to the next bar's open
    b2 = bars[~((bars.date == day) & (bars.permno == p) & (bars.b == n_t))]
    meta2, _ = _samples(b2, daily, n_t, day)
    r2 = meta2[meta2.permno == p]["ret"].iloc[0]
    assert np.isclose(r2, close / row_b.loc[n_t + 1, "open"] - 1)


def test_labels_split_each_day_at_the_median() -> None:
    bars, daily = _market()
    day = np.sort(bars["date"].unique())[20]
    meta, packed = _samples(bars, daily, DECISIONS["1030"], day)
    assert abs(meta["label"].mean() - 0.5) < 0.02
    assert packed.shape[1] == 64 * 3 * (78 + 12) // 8
