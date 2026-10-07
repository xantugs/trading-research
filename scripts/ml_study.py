#!/usr/bin/env python3
"""Run docs/ML_PLAN.md.

    python -m scripts.ml_study build   # decision rows, S2 trades and grids (HistData 2010-2024, FTMO 2025-26)
    python -m scripts.ml_study fit     # train on 2010-11..2017, choose p* and theta on 2018-2021
    python -m scripts.ml_study test    # once: sealed 2022-2024 and the FTMO 2025-26 check
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research.config import load_config
from orb_research.data_loader import load_m1
from orb_research.intraday_momentum import s2_noise_area, session_grid
from orb_research.ml_intraday import FEATURES, decision_frame, trade_direct
from orb_research.ml_models import Blend
from scripts.s2_basket_study import histdata_bars, spread_ratio, stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/ml"
FTMO = {"NSX": "configs/indices/ftmo_us100_orb_framework_15_15.json", "SPX": "configs/symbols/ftmo_us500_cash.json",
        "DAX": "configs/symbols/ftmo_ger40_cash.json", "GOLD": "configs/orb_framework.json"}
HIST = {"NSX": "NSXUSD", "SPX": "SPXUSD", "DAX": "GRXEUR", "GOLD": "XAUUSD"}
SPLITS = {"train": ("2010-11-01", "2018-01-01"), "valid": ("2018-01-01", "2022-01-01"),
          "test": ("2022-01-01", "2025-01-01"), "ftmo": ("2025-01-01", "2026-10-07")}
P_GRID, THETA_GRID = (0.40, 0.45, 0.50, 0.55), (0.00, 0.02, 0.05, 0.08)


def ftmo_m1(name: str, start: str) -> tuple[pd.DataFrame, np.ndarray]:
    config = load_config(ROOT / FTMO[name])
    m1, _ = load_m1(config)
    m1 = m1.loc[(m1.index >= pd.Timestamp(start, tz="UTC")) & (m1.index < pd.Timestamp("2026-10-07", tz="UTC"))]
    spread = m1.spread.to_numpy() * config.point
    return m1, np.where(spread > 0, spread, np.median(spread[spread > 0]))


def build_source(source: str) -> dict:
    """Grids, decision rows and S2 trades for NSX and SPX from one data source."""
    if source == "hist":
        bars = {k: histdata_bars(v, "2010-01-01", "2025-01-01") for k, v in HIST.items()}
        spreads = {k: spread_ratio(HIST[k]) * bars[k].close.to_numpy() for k in ("NSX", "SPX")}
    else:   # FTMO, with two months of warm-up before 2025 for the rolling features
        loaded = {k: ftmo_m1(k, "2024-10-01" if k in ("NSX", "SPX") else "2025-01-01") for k in FTMO}
        bars = {k: v[0] for k, v in loaded.items()}
        spreads = {k: loaded[k][1] for k in ("NSX", "SPX")}
    grids = {k: session_grid(bars[k], spreads[k]) for k in ("NSX", "SPX")}
    out = {"grids": grids}
    for flag, (name, other) in enumerate((("NSX", "SPX"), ("SPX", "NSX"))):
        close, spread, first = grids[name]
        frame = decision_frame(close, first, grids[other][0], grids[other][2], bars["DAX"].close, bars["GOLD"].close, flag)
        record: list = []
        daily = s2_noise_area(close, spread, first, record=record)
        trades = pd.DataFrame(record)
        trades["check"] = (trades.entry_minute - 600) // 30
        trades["market"] = flag
        out[name] = {"decisions": frame, "s2_trades": trades, "s2_daily": daily[["trades", "ret"]]}
    return out


def in_split(dates, split: str) -> np.ndarray:
    start, end = SPLITS[split]
    d = pd.to_datetime(pd.Series(dates)).to_numpy()
    keep = (d >= np.datetime64(start)) & (d < np.datetime64(end))
    if split in ("valid", "test"):     # 5 trading days dropped after each boundary
        first_days = np.sort(np.unique(d[keep]))[:5]
        keep &= ~np.isin(d, first_days)
    return keep


def meta_samples(data: dict) -> pd.DataFrame:
    """S2 entries joined to the decision features at the entry checkpoint."""
    parts = []
    for name in ("NSX", "SPX"):
        trades = data[name]["s2_trades"]
        rows = data[name]["decisions"].set_index(["date", "check"])
        joined = trades.join(rows[list(FEATURES[3:])], on=["date", "check"])   # market/check/dow come along below
        joined["dow"] = pd.to_datetime(joined.date).dt.dayofweek
        parts.append(joined)
    return pd.concat(parts, ignore_index=True)


def filtered_daily(base: pd.DataFrame, trades: pd.DataFrame, keep: np.ndarray) -> pd.DataFrame:
    """S2's daily returns with the skipped trades' returns removed."""
    skipped = trades.loc[~keep].groupby("date").ret.sum()
    kept_n = trades.loc[keep].groupby("date").size()
    out = base.copy()
    out["ret"] = out.ret - skipped.reindex(out.index, fill_value=0.0)
    out["trades"] = kept_n.reindex(out.index, fill_value=0)
    return out


def sharpe(frame: pd.DataFrame) -> float:
    r = frame.ret.to_numpy(float)
    return float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if len(r) > 1 and r.std() > 0 else -np.inf


def build() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for source in ("hist", "ftmo"):
        data = build_source(source)
        with open(OUT / f"data_{source}.pkl", "wb") as handle:
            pickle.dump(data, handle)
        for name in ("NSX", "SPX"):
            print(f"{source} {name}: {len(data[name]['decisions']):,} decision rows, "
                  f"{len(data[name]['s2_trades']):,} S2 trades", flush=True)
    return 0


def fit() -> int:
    with open(OUT / "data_hist.pkl", "rb") as handle:
        data = pickle.load(handle)
    choices = {}

    # Project B: meta-label S2 entries (NSX + SPX pooled)
    meta = meta_samples(data)
    x = meta[list(FEATURES)].to_numpy(np.float64)
    y = (meta.ret.to_numpy() > 0).astype(np.float32)[:, None]
    w = np.abs(meta.ret.to_numpy()) + 1e-6
    tr, va = in_split(meta.date, "train"), in_split(meta.date, "valid")
    blend_b = Blend().fit(x[tr], y[tr], w[tr], x[va], y[va], w[va])
    nsx_trades = data["NSX"]["s2_trades"]
    nsx_x = meta.loc[meta.market == 0, list(FEATURES)].to_numpy(np.float64)
    p_nsx = blend_b.predict(nsx_x)
    base = data["NSX"]["s2_daily"]
    valid_days = base[in_split(base.index, "valid")]
    scores = {}
    for p_star in P_GRID:
        keep = p_nsx >= p_star
        scores[p_star] = sharpe(filtered_daily(valid_days, nsx_trades, keep).loc[valid_days.index])
    choices["B_p_star"] = max(scores, key=scores.get)
    choices["B_valid_sharpe"] = {str(k): v for k, v in scores.items()}
    choices["B_valid_sharpe_unfiltered"] = sharpe(valid_days)
    print("B validation Sharpe by p*:", {k: round(v, 3) for k, v in scores.items()},
          "unfiltered:", round(choices["B_valid_sharpe_unfiltered"], 3), "-> p* =", choices["B_p_star"], flush=True)

    # Project A: direct predictor on NSX decision rows
    dec = data["NSX"]["decisions"]
    xa = dec[list(FEATURES)].to_numpy(np.float64)
    ya = np.stack([dec.fwd30 > 0, dec.fwd60 > 0, dec.fwd_close > 0], axis=1).astype(np.float32)
    wa = np.abs(dec[["fwd30", "fwd60", "fwd_close"]].to_numpy()).mean(axis=1) + 1e-6
    tr, va = in_split(dec.date, "train"), in_split(dec.date, "valid")
    ok = np.isfinite(dec[["fwd30", "fwd60", "fwd_close"]].to_numpy()).all(axis=1)
    blend_a = Blend().fit(xa[tr & ok], ya[tr & ok], wa[tr & ok], xa[va & ok], ya[va & ok], wa[va & ok])
    close, spread, _ = data["grids"]["NSX"]
    valid_rows = dec[va]
    p_valid = blend_a.predict(xa[va])
    scores = {theta: sharpe(trade_direct(valid_rows, p_valid, theta, close, spread)) for theta in THETA_GRID}
    choices["A_theta"] = max(scores, key=scores.get)
    choices["A_valid_sharpe"] = {str(k): v for k, v in scores.items()}
    print("A validation Sharpe by theta:", {k: round(v, 3) for k, v in scores.items()}, "-> theta =", choices["A_theta"], flush=True)

    with open(OUT / "models.pkl", "wb") as handle:
        pickle.dump({"B": blend_b, "A": blend_a}, handle)
    (OUT / "choices.json").write_text(json.dumps(choices, indent=2) + "\n", encoding="utf-8")
    return 0


def evaluate(data: dict, models: dict, choices: dict, split: str) -> dict:
    out = {}
    meta = meta_samples(data)
    nsx = meta[meta.market == 0]
    p = models["B"].predict(nsx[list(FEATURES)].to_numpy(np.float64))
    trades = data["NSX"]["s2_trades"]
    base = data["NSX"]["s2_daily"]
    days = base[in_split(base.index, split)]
    filtered = filtered_daily(days, trades, p >= choices["B_p_star"]).loc[days.index]
    in_days = trades.date.isin(days.index).to_numpy()
    out["B_unfiltered"] = stats(days.ret, days.trades)
    out["B_filtered"] = stats(filtered.ret, filtered.trades)
    out["B_trade_win_rate_unfiltered"] = float((trades.ret[in_days] > 0).mean())
    out["B_trade_win_rate_filtered"] = float((trades.ret[in_days & (p >= choices["B_p_star"])] > 0).mean())
    out["B_trades_kept"] = int((in_days & (p >= choices["B_p_star"])).sum())
    out["B_trades_total"] = int(in_days.sum())
    dec = data["NSX"]["decisions"]
    rows = dec[in_split(dec.date, split)]
    close, spread, _ = data["grids"]["NSX"]
    daily_a = trade_direct(rows, models["A"].predict(rows[list(FEATURES)].to_numpy(np.float64)), choices["A_theta"], close, spread)
    out["A"] = stats(daily_a.ret, daily_a.trades)
    out["A_win_rate_traded_days"] = float((daily_a.ret[daily_a.trades > 0] > 0).mean())
    return out


def test() -> int:
    target = OUT / "test_results.json"
    if target.exists():
        raise SystemExit(f"the sealed test has already been run once: {target}")
    choices = json.loads((OUT / "choices.json").read_text(encoding="utf-8"))
    with open(OUT / "models.pkl", "rb") as handle:
        models = pickle.load(handle)
    with open(OUT / "data_hist.pkl", "rb") as handle:
        hist = pickle.load(handle)
    with open(OUT / "data_ftmo.pkl", "rb") as handle:
        ftmo = pickle.load(handle)
    results = {"test": evaluate(hist, models, choices, "test"), "ftmo": evaluate(ftmo, models, choices, "ftmo")}
    t, f = results["test"], results["ftmo"]
    b_pass = (t["B_filtered"]["mean_bps"] >= t["B_unfiltered"]["mean_bps"]
              and t["B_filtered"]["sharpe"] >= t["B_unfiltered"]["sharpe"] + 0.20
              and t["B_trade_win_rate_filtered"] > t["B_trade_win_rate_unfiltered"])
    b_confirm = f["B_filtered"]["sharpe"] >= f["B_unfiltered"]["sharpe"]
    years_up = sum(v > 0 for v in t["A"]["yearly_bps"].values())
    a_pass = t["A"]["mean_bps"] > 0 and (t["A"]["t"] or 0) >= 2.0 and years_up >= 2
    a_confirm = f["A"]["mean_bps"] > 0
    results["verdict"] = {"B": ("pass" if b_confirm else "pass test, not confirmed by FTMO") if b_pass else "fail",
                          "A": ("pass" if a_confirm else "pass test, not confirmed by FTMO") if a_pass else "fail"}
    target.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for split, r in results.items():
        if split == "verdict":
            continue
        print(f"[{split}] B unfiltered {r['B_unfiltered']['mean_bps']:+.2f} bps Sharpe {r['B_unfiltered']['sharpe']:+.2f} "
              f"win {r['B_trade_win_rate_unfiltered']:.0%} | filtered {r['B_filtered']['mean_bps']:+.2f} bps Sharpe "
              f"{r['B_filtered']['sharpe']:+.2f} win {r['B_trade_win_rate_filtered']:.0%} kept {r['B_trades_kept']}/{r['B_trades_total']}")
        print(f"[{split}] A {r['A']['mean_bps']:+.2f} bps/day t={r['A']['t']:+.2f} Sharpe {r['A']['sharpe']:+.2f} "
              f"win(traded days) {r['A_win_rate_traded_days']:.0%} trades {r['A']['days_traded']}/{r['A']['days']} days")
    print("verdict:", results["verdict"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("build", "fit", "test"))
    return {"build": build, "fit": fit, "test": test}[parser.parse_args(argv).step]()


if __name__ == "__main__":
    raise SystemExit(main())
