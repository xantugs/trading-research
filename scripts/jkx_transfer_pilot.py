"""Pilot A (docs/JKX_TRANSFER_PILOT.md): frozen JKX CNNs on FTMO daily charts.

    .venv/Scripts/python -m scripts.jkx_transfer_pilot

Reads C:/data/ftmo_d1 (scripts.download_mt5_d1) and the frozen seed files in
C:/data/jkx/models. Writes outputs/jkx/transfer_pilot.md and one ledger row.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from orb_research.jkx_cnn import predict
from orb_research.jkx_images import Panel, build_samples, formation_dates
from orb_research.jkx_portfolio import summarize
from orb_research.vault import record_run

FIRST, LAST = pd.Timestamp("2010-01-01"), pd.Timestamp("2025-12-31")
GROUP_OF = {"Forex": "FX", "Exotics": "FX", "Metals CFD": "metals", "Cash CFD": "indices", "Cash II CFD": "indices",
            "Cash III CFD": "indices", "Commodities": "energy/agri", "Agriculture": "energy/agri",
            "Crypto I CFD": "crypto", "Crypto II CFD": "crypto", "Equities I CFD": "stocks (control)",
            "Equities II CFD": "stocks (control)"}
ENERGY_CASH = {"USOIL.cash", "UKOIL.cash"}     # oil sits in the Cash II group but is energy
MIN_NAMES = 10


def single_panel(bars: pd.DataFrame) -> Panel:
    """One instrument as a one-column Panel; a bar on a day means 'listed' that day."""
    bars = bars.sort_values("date").drop_duplicates("date")
    col = lambda x: x.to_numpy(np.float64)[:, None]        # noqa: E731
    close = bars["close"].where(bars["close"] > 0)
    ret = close.pct_change()
    return Panel(dates=pd.DatetimeIndex(bars["date"]), permnos=np.array([0]), open=col(bars["open"]),
                 high=col(bars["high"]), low=col(bars["low"]), close=col(close),
                 volume=col(bars["tick_volume"].astype(float)), ret=col(ret), total_ret=col(ret),
                 mcap=np.ones((len(bars), 1)), listed=np.ones((len(bars), 1), bool))


def samples_for(bars: pd.DataFrame, days: int, freq: str) -> tuple[pd.DataFrame, np.ndarray]:
    panel = single_panel(bars)
    form = formation_dates(panel.dates, freq)
    form = form[form < panel.dates[-1]]                     # the last period may be cut off
    build = form[(form >= FIRST) & (form <= LAST)]
    s = build_samples(panel, days, form, build=build, data_end=LAST)
    if not len(s.meta):
        return pd.DataFrame(), s.packed
    # realised volatility over the 60 bars ending at formation, annualised by bars per year
    r = pd.Series(panel.ret[:, 0], index=panel.dates)
    per_year = 365 if (panel.dates.dayofweek >= 5).mean() > 0.1 else 252
    vol = (r.rolling(60, min_periods=40).std() * np.sqrt(per_year)).reindex(s.meta["date"]).to_numpy()
    return s.meta.assign(vol=vol), s.packed


def ensemble(models: Path, name: str, packed: np.ndarray, days: int) -> np.ndarray:
    fits = [torch.load(p, weights_only=False) for p in sorted(models.glob(f"{name}_seed*.pt"))]
    if len(fits) != 5:
        raise SystemExit(f"{name}: expected 5 seeds, found {len(fits)}")
    return np.mean([predict(f, packed, days) for f in fits], axis=0)


def rank_ic(frame: pd.DataFrame, score: str, ret: str = "hold_ret") -> pd.Series:
    """Per-date Spearman correlation (Pearson on ranks) with at least MIN_NAMES instruments."""
    def one(g: pd.DataFrame) -> float:
        g = g.dropna(subset=[score, ret])
        if len(g) < MIN_NAMES:
            return np.nan
        return float(np.corrcoef(g[score].rank(), g[ret].rank())[0, 1])
    return frame.groupby("date")[[score, ret]].apply(one).dropna()


def sign_strategy(frame: pd.DataFrame, score: str, centre: float) -> pd.Series:
    """Long if score > centre else short, each position scaled to 10% annualised volatility,
    equal weight across instruments; one return per formation date."""
    f = frame.dropna(subset=[score, "hold_ret", "vol"])
    f = f[f["vol"] > 0]
    pos = np.where(f[score] > centre, 1.0, -1.0) * 0.10 / f["vol"]
    return (pos * f["hold_ret"]).groupby(f["date"]).mean()


def describe(series: pd.Series, freq: str) -> dict:
    if len(series) < 10:
        return {"n": len(series)}
    s = summarize(series, freq)
    return {"n": s["periods"], "mean": float(series.mean()), "t_nw": s["t_nw"], "sharpe": s["sharpe"]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bars", type=Path, default=Path("C:/data/ftmo_d1"))
    p.add_argument("--models", type=Path, default=Path("C:/data/jkx/models"))
    p.add_argument("--out", type=Path, default=Path("outputs/jkx"))
    a = p.parse_args()
    manifest = json.loads((a.bars / "manifest.json").read_text())
    runs = [("I5R5", 5, "week", 0.5)]
    if (a.models / "I20R20_seed0.pt").exists():
        runs.append(("I20R20", 20, "month", 0.5))
    if (a.models / "I20R5_seed0.pt").exists():
        runs.append(("I20R5", 20, "week", 0.5))

    lines, metrics = ["# Pilot A results (docs/JKX_TRANSFER_PILOT.md)", ""], {}
    for name, days, freq, centre in runs:
        metas, packs = [], []
        for sym, info in manifest["symbols"].items():
            bars = pd.read_parquet(a.bars / f"{sym}.parquet")
            meta, packed = samples_for(bars, days, freq)
            if not len(meta):
                continue
            group = "energy/agri" if sym in ENERGY_CASH else GROUP_OF[info["group"]]
            metas.append(meta.assign(symbol=sym, group=group))
            packs.append(packed)
        meta = pd.concat(metas, ignore_index=True)
        meta["prob"] = ensemble(a.models, name, np.concatenate(packs), days)
        meta.to_parquet(a.out / f"transfer_{name}.parquet", index=False)
        test = meta[meta["group"] != "stocks (control)"]
        control = meta[meta["group"] == "stocks (control)"]

        lines += [f"## {name} ({freq}ly formation, {meta['date'].min().date()} .. {meta['date'].max().date()})", "",
                  f"Instruments: {test['symbol'].nunique()} test + {control['symbol'].nunique()} stock CFDs; "
                  f"{len(test):,} test images.", "",
                  "| Sample | Signal | IC periods | mean IC | IC t (NW) | sign-strategy Sharpe | sign t (NW) |",
                  "|---|---|---|---|---|---|---|"]
        for label, frame in [("ALL non-stock (primary)" if name == "I5R5" else "ALL non-stock", test),
                             *[(g, test[test["group"] == g]) for g in sorted(test["group"].unique())],
                             ("stocks (control)", control)]:
            if frame.empty:
                continue
            for sig, col, c in [("CNN", "prob", centre), ("1-week reversal", "wstr", 0.0), ("12-month momentum", "mom", 0.0)]:
                ic = describe(rank_ic(frame, col), freq)
                ts = describe(sign_strategy(frame, col, c), freq)
                metrics[f"{name}|{label}|{sig}"] = {"ic": ic, "sign": ts}
                fmt = lambda d, k: f"{d[k]:.3f}" if k in d else "n/a"     # noqa: E731
                lines.append(f"| {label} | {sig} | {ic.get('n', 0)} | {fmt(ic, 'mean')} | {fmt(ic, 't_nw')} | "
                             f"{fmt(ts, 'sharpe')} | {fmt(ts, 't_nw')} |")
        lines.append("")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "transfer_pilot.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    record_run("jkx/transfer_pilot", {"runs": [r[0] for r in runs], "window": [str(FIRST.date()), str(LAST.date())]},
               metrics)


if __name__ == "__main__":
    main()
