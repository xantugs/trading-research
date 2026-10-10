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


def _synthetic_meta(rng, year, n_days=30, n_stocks=60):
    dates = pd.bdate_range(f"{year}-01-02", periods=n_days)
    rows = []
    for d in dates:
        ret = rng.normal(0, 0.01, n_stocks)
        label = (ret > np.median(ret)).astype(np.int8)
        rows.append(pd.DataFrame({"date": d, "permno": 10_000 + np.arange(n_stocks), "mcap": rng.uniform(1e9, 1e11, n_stocks),
                                  "ret": ret, "label": label, "mom": rng.normal(size=n_stocks), "rev": -ret + rng.normal(size=n_stocks),
                                  "s2": rng.normal(size=n_stocks), "wrev": rng.normal(size=n_stocks), "hks": rng.normal(size=n_stocks),
                                  "rv": rng.uniform(size=n_stocks).astype(np.float32)}))
    return pd.concat(rows, ignore_index=True)


def test_cnn_evaluation_reports_the_pass_condition_and_runs_the_test_once(tmp_path, monkeypatch, capsys) -> None:
    from scripts import intraday_b_eval as ev

    rng = np.random.default_rng(0)
    (tmp_path / "T1200").mkdir()
    (tmp_path / "predictions").mkdir()
    dev, test = [], []
    for y in range(2012, 2021):
        m = _synthetic_meta(rng, y)
        m.to_parquet(tmp_path / "T1200" / f"{y}_meta.parquet", index=False)
        (dev if y < 2018 else test).append(m)
    for part, frames in (("dev", dev), ("test", test)):
        m = pd.concat(frames, ignore_index=True)
        m = m.assign(prob=m["ret"] + rng.normal(0, 0.01, len(m)))       # a score with some foresight
        m.to_parquet(tmp_path / "predictions" / f"T1200_{part}.parquet", index=False)
    ledger = []
    monkeypatch.setattr(ev, "DATA", tmp_path)
    monkeypatch.setattr(ev, "record_run", lambda exp, cfg, met: ledger.append((exp, met)))
    monkeypatch.setattr(ev, "n_runs", lambda prefix: sum(e.startswith(prefix) for e, _ in ledger))
    monkeypatch.chdir(tmp_path)

    ev.cnn("1200", "dev")
    out = capsys.readouterr().out
    assert "cnn" in out and ledger[-1][0] == "intraday_b/dev_T1200"
    assert (tmp_path / "outputs" / "intraday_b" / "cnn_dev_T1200.csv").exists()

    ev.cnn("1200", "test")
    out = capsys.readouterr().out
    assert "T1200 test 2018-2020:" in out and "best baseline" in out
    table = pd.read_csv(tmp_path / "outputs" / "intraday_b" / "cnn_test_T1200.csv")
    assert set(table["score"]) == {"cnn", "mom", "rev", "s2", "wrev", "hks", "logit"}
    assert ledger[-1][1]["verdict"] in ("PASS", "FAIL")
    assert ledger[-1][1]["ew_net_t"] > 3          # foresight score must pass check 1 on synthetic data

    import pytest
    with pytest.raises(SystemExit, match="already been evaluated"):
        ev.cnn("1200", "test")
