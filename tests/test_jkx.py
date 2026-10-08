"""JKX replication: image drawing, CRSP panel handling, point-in-time samples, portfolios,
the vault, and the CNN against the paper's exact parameter counts."""
from __future__ import annotations

import json
import subprocess

import numpy as np
import pandas as pd
import pytest

from orb_research.jkx_images import (build_samples, draw, formation_dates, image_shape, pack,
                                     panel_from_crsp, unpack)
from orb_research.jkx_portfolio import decile_returns, summarize
from orb_research import vault

NAN = np.nan


# ------------------------------------------------------------------------------- drawing

def _rand_inputs(rng, b=6, n=20):
    close = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, (b, n)), axis=1))
    high = close * (1 + rng.uniform(0, 0.03, (b, n)))
    low = close * (1 - rng.uniform(0, 0.03, (b, n)))
    open_ = low + (high - low) * rng.uniform(0, 1, (b, n))
    ma = close.copy()
    for k in range(n):
        ma[:, k] = close[:, max(0, k - n + 1):k + 1].mean(axis=1)
    vol = rng.uniform(1e5, 1e6, (b, n))
    return open_, high, low, close, ma, vol


def test_draw_layout_follows_the_paper() -> None:
    high = np.array([[2.0, 3, 4, 5, 6]])
    low = np.array([[1.0, 2, 3, 4, 5]])
    open_ = low + 0.4
    close = high - 0.2
    ma = np.full((1, 5), NAN)
    vol = np.array([[1.0, 2, 3, 4, 5]])
    img, valid = draw(open_, high, low, close, ma, vol)
    assert valid.all() and img.shape == (1, 32, 15)
    price_h, vol_h = 26, 6
    px = img[0]
    assert px[0, 13] == 255 and px[price_h - 1, 1] == 255          # max high at the top, min low at the bottom
    for d in range(5):
        assert (px[:price_h, 3 * d] == 255).sum() == 1             # one open tick, left column
        assert (px[:price_h, 3 * d + 2] == 255).sum() == 1         # one close tick, right column
        row = price_h - 1 - int(np.rint((open_[0, d] - 1) / 5 * (price_h - 1)))
        assert px[row, 3 * d] == 255
    assert (px[price_h:, 13] == 255).all()                         # largest volume fills the volume area
    assert px[31, 1] == 255 and px[30, 1] == 0                     # 1/5 of the max volume: one pixel of six
    assert (px[price_h:, [0, 2]] == 0).all()                       # volume only in centre columns
    assert vol_h == 32 // 5


def test_images_ignore_the_price_level() -> None:
    o, h, lo, c, ma, v = _rand_inputs(np.random.default_rng(0))
    a, _ = draw(o, h, lo, c, ma, v)
    b, _ = draw(7 * o, 7 * h, 7 * lo, 7 * c, 7 * ma, 3 * v)
    assert (a == b).all()


def test_missing_high_or_low_leaves_the_day_black() -> None:
    o, h, lo, c, ma, v = _rand_inputs(np.random.default_rng(1), b=1, n=5)
    h[0, 2] = NAN
    img, _ = draw(o, h, lo, c, np.full_like(ma, NAN), np.full_like(v, NAN))
    assert (img[0][:, 6:9] == 0).all()


def test_moving_average_line_is_connected() -> None:
    n = 20
    high, low = np.full((1, n), NAN), np.full((1, n), NAN)
    high[0, 0], low[0, 0] = 1.0, 1.0 - 1e-9                        # one tiny bar so the image is valid
    ma = np.array([1.0 + ((k * 7) % 11) for k in range(n)])[None, :]   # jagged, steep line
    img, valid = draw(np.full((1, n), NAN), high, low, np.full((1, n), NAN), ma, np.full((1, n), NAN))
    assert valid.all()
    price = img[0, : 64 - 64 // 5]
    prev = None
    for col in range(2, 3 * (n - 1) + 2):
        rows = np.flatnonzero(price[:, col])
        assert len(rows), f"gap in column {col}"
        if prev is not None:
            assert rows.min() <= prev.max() + 1 and rows.max() >= prev.min() - 1
        prev = rows


def test_pack_round_trip() -> None:
    o, h, lo, c, ma, v = _rand_inputs(np.random.default_rng(2), b=4, n=60)
    img, _ = draw(o, h, lo, c, ma, v)
    assert (unpack(pack(img), 60) == img).all()


# --------------------------------------------------------------------------------- panel

def _crsp(n_stocks=12, n_days=420, seed=0, start="2000-01-03") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n_days)
    rows = []
    for s in range(n_stocks):
        prc = 20 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
        ret = np.r_[NAN, prc[1:] / prc[:-1] - 1]
        hi = prc * (1 + rng.uniform(0, 0.02, n_days))
        lo = prc * (1 - rng.uniform(0, 0.02, n_days))
        rows.append(pd.DataFrame({"permno": 10000 + s, "date": dates, "prc": prc, "openprc": (hi + lo) / 2,
                                  "askhi": hi, "bidlo": lo, "vol": rng.uniform(1e4, 1e5, n_days), "ret": ret,
                                  "shrout": 1000.0 + s, "cfacpr": 1.0, "cfacshr": 1.0, "exchcd": 1, "shrcd": 10}))
    return pd.concat(rows, ignore_index=True)


def _samples(daily, days=5, freq="week", delist=None, data_end=None):
    panel = panel_from_crsp(daily, delist)
    form = formation_dates(panel.dates, freq)
    return panel, build_samples(panel, days, form[form < panel.dates[-1]], data_end=data_end)


def test_a_split_does_not_change_the_image() -> None:
    daily = _crsp(n_stocks=2, n_days=80)
    a = daily[daily.permno == 10000].copy()
    b = a.copy()
    b["permno"] = 20000
    split = b["date"] >= b["date"].iloc[60]
    for col in ("prc", "openprc", "askhi", "bidlo"):
        b.loc[split, col] /= 2                     # 2-for-1 split: prices halve, CRSP's ret already adjusts
    b.loc[split, "vol"] *= 2
    b.loc[~split, "cfacshr"] = 2.0                 # earlier share counts are doubled to compare
    b.loc[split, "shrout"] *= 2
    _, s = _samples(pd.concat([a, b]), days=20, freq="week")
    m = s.meta.reset_index(drop=True)
    for date in m["date"].unique():
        i = m.index[(m.date == date) & (m.permno == 10000)]
        j = m.index[(m.date == date) & (m.permno == 20000)]
        assert (unpack(s.packed[i], 20) == unpack(s.packed[j], 20)).all()
        assert np.isclose(m.loc[i[0], "ret_5d"], m.loc[j[0], "ret_5d"], equal_nan=True)


def test_samples_use_nothing_after_the_formation_date() -> None:
    daily = _crsp()
    _, before = _samples(daily, days=20, freq="month")
    t = before.meta["date"].unique()[4]
    later = daily["date"] > t
    rng = np.random.default_rng(9)
    changed = daily.copy()
    for col in ("prc", "openprc", "askhi", "bidlo", "vol", "ret", "shrout"):
        changed.loc[later, col] = changed.loc[later, col] * rng.uniform(0.5, 1.5, later.sum())
    _, after = _samples(changed, days=20, freq="month")
    mb, ma_ = before.meta[before.meta.date == t], after.meta[after.meta.date == t]
    assert len(mb) == len(ma_) > 0
    pd.testing.assert_frame_equal(mb[["permno", "mcap", "wstr", "str", "mom"]].reset_index(drop=True),
                                  ma_[["permno", "mcap", "wstr", "str", "mom"]].reset_index(drop=True))
    assert (before.packed[mb.index] == after.packed[ma_.index]).all()
    assert not np.allclose(mb["ret_20d"], ma_["ret_20d"])              # labels do look ahead, by design


def test_ipos_and_delistings_inside_the_window_are_excluded() -> None:
    daily = _crsp(n_stocks=3, n_days=120)
    dates = np.sort(daily.date.unique())
    late_ipo = (daily.permno == 10001) & (daily.date < dates[100])
    gone = (daily.permno == 10002) & (daily.date > dates[60])
    _, s = _samples(daily[~late_ipo & ~gone], days=20, freq="week")
    m = s.meta
    t = pd.Timestamp(dates[105])
    first_after = m.loc[m.date >= t, "date"].min()
    assert 10001 not in set(m.loc[m.date == first_after, "permno"])    # listed for under 20 days
    assert 10002 not in set(m.loc[m.date > pd.Timestamp(dates[60]), "permno"])


def test_delisting_return_lands_in_the_next_holding_period() -> None:
    daily = _crsp(n_stocks=2, n_days=120)
    panel = panel_from_crsp(daily)
    form = formation_dates(panel.dates, "week")
    t = form[10]
    cut = daily[~((daily.permno == 10001) & (daily.date > t))]
    delist = pd.DataFrame({"permno": [10001], "dlstdt": [t + pd.Timedelta(days=1)], "dlret": [-0.5]})
    _, s = _samples(cut, days=5, freq="week", delist=delist)
    row = s.meta[(s.meta.date == t) & (s.meta.permno == 10001)].iloc[0]
    assert np.isclose(row["hold_ret"], -0.5) and np.isclose(row["ret_5d"], -0.5)


def test_forward_values_past_the_data_end_are_nan_not_zero() -> None:
    daily = _crsp(n_stocks=3, n_days=120)
    end = pd.Timestamp(np.sort(daily.date.unique())[-10])
    _, s = _samples(daily, days=5, freq="week", data_end=end)
    late = s.meta[s.meta.date > end - pd.Timedelta(days=5)]
    assert len(late) and late["ret_5d"].isna().all()


# ---------------------------------------------------------------------------- portfolios

def test_deciles_on_a_planted_signal() -> None:
    rng = np.random.default_rng(3)
    rows = []
    for date in pd.date_range("2001-01-05", periods=150, freq="W-FRI"):
        signal = rng.normal(size=300)
        rows.append(pd.DataFrame({"date": date, "permno": np.arange(300), "mcap": rng.uniform(1, 100, 300),
                                  "score": signal, "noise": rng.normal(size=300),
                                  "hold_ret": 0.002 * signal + rng.normal(0, 0.03, 300)}))
    f = pd.concat(rows, ignore_index=True)
    good = decile_returns(f, "score")
    assert list(good.columns[:10]) == list(range(1, 11))
    assert summarize(good["H-L"], "week")["sharpe"] > 3
    assert abs(summarize(decile_returns(f, "noise")["H-L"], "week")["t"]) < 3
    assert summarize(decile_returns(f, "score", weight="mcap", top=100)["H-L"], "week")["sharpe"] > 1


# --------------------------------------------------------------------------------- vault

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_vault_refuses_sealed_reads_until_a_committed_plan_unseals(tmp_path) -> None:
    vf, log = tmp_path / "vault.json", tmp_path / "log.jsonl"
    vf.write_text(json.dumps({"ranges": [{"name": "late", "start": "2020-01-01", "end": "2099-12-31",
                                          "datasets": ["crsp_daily"]}]}))
    vault.check("crsp_daily", "2001-01-01", "2019-12-31", vault_file=vf, log=log)       # open range: fine
    with pytest.raises(vault.VaultError):
        vault.check("crsp_daily", "2019-06-01", "2020-02-01", "exp1", vault_file=vf, log=log)

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    plan = tmp_path / "plan.md"
    plan.write_text("plan")
    with pytest.raises(vault.VaultError, match="not committed"):
        vault.unseal("exp1", "late", plan, root=tmp_path, vault_file=vf, log=log)
    _git(tmp_path, "add", "plan.md")
    _git(tmp_path, "commit", "-qm", "plan")
    plan.write_text("edited after commit")
    with pytest.raises(vault.VaultError, match="uncommitted"):
        vault.unseal("exp1", "late", plan, root=tmp_path, vault_file=vf, log=log)
    plan.write_text("plan")
    vault.unseal("exp1", "late", plan, root=tmp_path, vault_file=vf, log=log)
    vault.check("crsp_daily", "2019-06-01", "2020-02-01", "exp1", vault_file=vf, log=log)
    with pytest.raises(vault.VaultError, match="already"):
        vault.unseal("exp1", "late", plan, root=tmp_path, vault_file=vf, log=log)
    with pytest.raises(vault.VaultError):
        vault.check("crsp_daily", "2020-01-01", "2020-02-01", "exp2", vault_file=vf, log=log)
    events = [json.loads(x)["event"] for x in log.read_text().splitlines()]
    assert events == ["unseal", "read"]


# ----------------------------------------------------------------------------------- CNN

torch = pytest.importorskip("torch")


def test_cnn_matches_the_papers_parameter_counts() -> None:
    from orb_research.jkx_cnn import PAPER_FC_INPUTS, PAPER_PARAMS, ChartCNN, n_params
    for days in (5, 20, 60):
        m = ChartCNN(days)
        assert m.fc_inputs == PAPER_FC_INPUTS[days]
        assert n_params(m) == PAPER_PARAMS[days]
        assert m(torch.zeros(3, 1, *image_shape(days))).shape == (3, 2)


def test_cnn_learns_a_planted_chart_pattern() -> None:
    from orb_research.jkx_cnn import predict, train_one
    rng = np.random.default_rng(4)
    n = 1500
    o, h, lo, c, ma, v = _rand_inputs(rng, b=n, n=5)
    y = (c[:, -1] > c[:, 0]).astype(np.int64)                         # "the chart went up"
    img, valid = draw(o, h, lo, c, ma, v)
    packed = pack(img[valid])
    fit = train_one(packed, y[valid], 5, seed=0, lr=1e-3, max_epochs=6, log=lambda s: None)
    p = predict(fit, packed, 5)
    assert ((p > 0.5) == y[valid]).mean() > 0.8
