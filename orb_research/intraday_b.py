"""Idea B samples (docs/INTRADAY_CHARTS_PLAN.md, pre-registered 10 October 2026): intraday chart
images, labels and baseline scores for S&P 500 members at decision times 10:30, 12:00 and 14:00.

Timing: bar b covers [09:30 + 5b, 09:35 + 5b) and its close is known at 09:35 + 5b. At decision
time T, today's usable bars are 0..n_T-1 with n_T = (T - 09:30) / 5 minutes. Everything that
scores or draws a stock at T uses only those bars, earlier days, and CRSP data up to yesterday.
The label uses the entry (the open of the first bar at or after n_T, i.e. the first trade after
the decision) and today's official CRSP close (a market-on-close exit).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .jkx_images import draw, pack

N_BARS = 78
HEIGHT = 64
DECISIONS = {"1030": 12, "1200": 30, "1400": 54}      # n_T = number of today's bars known at T
FIELDS = ("open", "high", "low", "close", "volume", "vwap")


@dataclass
class Cube:
    """Bars as arrays [day, stock, bar] plus CRSP daily arrays [day, stock]."""
    dates: pd.DatetimeIndex
    permnos: np.ndarray
    bars: dict          # field -> float32 [D, P, 78]
    crsp_close: np.ndarray
    crsp_open: np.ndarray
    crsp_ret: np.ndarray
    mcap: np.ndarray


def make_cube(bars: pd.DataFrame, daily: pd.DataFrame) -> Cube:
    """`bars`: TAQ 5-minute bars (date, permno, b, open, high, low, close, volume, vwap).
    `daily`: CRSP rows (date, permno, prc, openprc, ret, shrout) covering the same dates."""
    dates = pd.DatetimeIndex(np.sort(bars["date"].unique()))
    permnos = np.sort(bars["permno"].unique())
    di = dates.get_indexer(bars["date"])
    pi = np.searchsorted(permnos, bars["permno"].to_numpy())
    bi = bars["b"].to_numpy().astype(int)
    keep = (bi >= 0) & (bi < N_BARS)
    cube = {}
    for f in FIELDS:
        a = np.full((len(dates), len(permnos), N_BARS), np.nan, np.float32)
        a[di[keep], pi[keep], bi[keep]] = bars[f].to_numpy()[keep]
        cube[f] = a
    d = daily[daily["permno"].isin(permnos) & daily["date"].isin(dates)]
    ddi, dpi = dates.get_indexer(d["date"]), np.searchsorted(permnos, d["permno"].to_numpy())

    def grid(values) -> np.ndarray:
        g = np.full((len(dates), len(permnos)), np.nan)
        g[ddi, dpi] = values
        return g

    prc = d["prc"].abs().where(d["prc"].abs() > 0).to_numpy()
    opn = d["openprc"].abs().where(d["openprc"].abs() > 0).to_numpy()
    return Cube(dates, permnos, cube, grid(prc), grid(opn), grid(d["ret"].to_numpy()),
                grid(prc * d["shrout"].where(d["shrout"] > 0).to_numpy()))


def _running_vwap(vwap: np.ndarray, vol: np.ndarray) -> np.ndarray:
    """Running volume-weighted price within each day; vwap/vol are [stocks, bars] of one day."""
    v = np.where(np.isfinite(vol) & np.isfinite(vwap), vol, 0.0)
    pv = np.where(v > 0, vwap * v, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.cumsum(pv, axis=1) / np.cumsum(v, axis=1)
    return np.where(np.cumsum(v, axis=1) > 0, out, np.nan)


def _first_open_from(op: np.ndarray, start: int) -> np.ndarray:
    """Open of the first bar at or after `start` for each stock (NaN if none)."""
    tail = op[:, start:]
    has = np.isfinite(tail)
    first = np.argmax(has, axis=1)
    out = tail[np.arange(len(tail)), first]
    return np.where(has.any(axis=1), out, np.nan)


def day_returns(cube: Cube, n_t: int) -> np.ndarray:
    """[D, P] return from the first trade at or after bar n_t to the CRSP close (the label return)."""
    entry = np.stack([_first_open_from(cube.bars["open"][k], n_t) for k in range(len(cube.dates))])
    entry = np.where(np.isfinite(entry), entry, cube.crsp_close)        # no trade after T: flat
    with np.errstate(invalid="ignore", divide="ignore"):
        return cube.crsp_close / entry - 1


def move_from_open(cube: Cube, n_t: int) -> np.ndarray:
    """[D, P] close of the bar ending at T over the day's official (CRSP opening-auction) open,
    minus 1. Known at T. The official open, not the first trade, which on volatile mornings can
    print on another venue before the auction (plan amendment 1)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return cube.bars["close"][:, :, n_t - 1] / cube.crsp_open[:, :] - 1


def build_samples(cube: Cube, n_t: int, build_dates: pd.DatetimeIndex, min_coverage: float = 0.9):
    """Images, labels and baseline scores for every eligible stock on each date in `build_dates`.

    Returns (meta DataFrame, packed images). `cube` must include enough earlier days for the
    lookbacks (yesterday's bars, 14 days for S2, 5 for periodicity, 5 for 1-week reversal)."""
    D = len(cube.dates)
    r_all = day_returns(cube, n_t)                     # label returns, known only after the close
    mv = move_from_open(cube, n_t)                     # known at T
    abs_mv = np.abs(mv)
    metas, packs = [], []
    for d in range(1, D):
        if cube.dates[d] not in build_dates:
            continue
        c_y, c_t = cube.bars["close"][d - 1], cube.bars["close"][d][:, :n_t]
        window_close = np.concatenate([c_y, c_t], axis=1)
        coverage = np.isfinite(window_close).mean(axis=1)
        ok = ((coverage >= min_coverage) & np.isfinite(cube.bars["close"][d][:, n_t - 1])
              & np.isfinite(cube.crsp_close[d]) & np.isfinite(cube.mcap[d - 1]) & np.isfinite(cube.crsp_close[d - 1]))
        idx = np.flatnonzero(ok)
        if len(idx) < 50:
            continue

        def win(f: str) -> np.ndarray:
            return np.concatenate([cube.bars[f][d - 1][idx], cube.bars[f][d][idx, :n_t]], axis=1)

        vw = np.concatenate([_running_vwap(cube.bars["vwap"][d - 1][idx], cube.bars["volume"][d - 1][idx]),
                             _running_vwap(cube.bars["vwap"][d][idx, :n_t], cube.bars["volume"][d][idx, :n_t])], axis=1)
        img, valid = draw(win("open"), win("high"), win("low"), win("close"), vw, win("volume"), height=HEIGHT)
        idx, img = idx[valid], img[valid]
        if len(idx) < 50:
            continue
        r = r_all[d, idx]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y = (r > np.nanmedian(r)).astype(np.int8)
            # baselines, each known at T
            price_t = cube.bars["close"][d][idx, n_t - 1]
            mom = price_t / cube.crsp_close[d - 1, idx] - 1
            rev = -mv[d, idx]
            lo = max(0, d - 14)
            hist = abs_mv[lo:d, idx]
            n_hist = np.isfinite(hist).sum(axis=0)
            s2 = np.where(n_hist >= 10, mv[d, idx] / np.nanmean(hist, axis=0), np.nan)
            wk = cube.crsp_ret[max(0, d - 5):d, idx]
            wrev = np.where(np.isfinite(wk).sum(axis=0) >= 4, -(np.nanprod(1 + wk, axis=0) - 1), np.nan)
            past = r_all[max(0, d - 5):d, idx]
            hks = np.where(np.isfinite(past).sum(axis=0) >= 3, np.nanmean(past, axis=0), np.nan)
            rets = np.diff(np.log(c_t[idx]), axis=1)
            rv = np.nanstd(rets, axis=1) if n_t > 2 else np.full(len(idx), np.nan)
        metas.append(pd.DataFrame({
            "date": cube.dates[d], "permno": cube.permnos[idx], "mcap": cube.mcap[d - 1, idx],
            "ret": r, "label": np.where(np.isfinite(r), y, -1).astype(np.int8),
            "mom": mom, "rev": rev, "s2": s2, "wrev": wrev, "hks": hks, "rv": rv}))
        packs.append(pack(img))
    if not metas:
        return pd.DataFrame(), np.zeros((0, HEIGHT * 3 * (N_BARS + n_t) // 8), np.uint8)
    return pd.concat(metas, ignore_index=True), np.concatenate(packs)
