"""Chart images in the style of Jiang, Kelly & Xiu (2023), "(Re-)Imag(in)ing Price Trends",
built from CRSP daily data (docs/JKX_REPLICATION_PLAN.md).

An n-day image is 3n pixels wide. Each day has an open tick (left column), a high-low bar
(centre column) and a close tick (right column). The n-day moving average of the close is a
line through the centre columns, and volume bars fill the bottom fifth. Prices are drawn on
a path built from CRSP returns, so splits and dividends do not move the chart. Each image is
scaled so its lowest and highest values touch the bottom and top of the price area.

Everything about a sample formed at date t (image, benchmarks, market cap) uses data up to
and including t. Labels and holding returns use only data after t.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

HEIGHT = {5: 32, 20: 64, 60: 96}
HORIZONS = (5, 20, 60)
FREQ_OF_HORIZON = {5: "week", 20: "month", 60: "quarter"}
EXCHANGES = (1, 2, 3)            # NYSE, AMEX, NASDAQ
MOM_FROM, MOM_TO = 251, 21       # momentum: return from t-251 to t-21 trading days (months t-12 to t-2)


def image_shape(days: int) -> tuple[int, int]:
    return HEIGHT[days], 3 * days


# ---------------------------------------------------------------------------------- drawing

def _rows(values: np.ndarray, lo: np.ndarray, span: np.ndarray, price_h: int) -> np.ndarray:
    """Pixel row (0 = top) of each value. NaN stays NaN."""
    return price_h - 1 - np.rint((values - lo[:, None]) / span[:, None] * (price_h - 1))


def draw(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, ma: np.ndarray,
         volume: np.ndarray, volume_bars: bool = True, height: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Draw a batch of images. Every input is (images, days); NaN marks a missing value.

    A day without both high and low is left black (the bar cannot be drawn, paper fn. 5); with
    high and low but no open or close, only the bar is drawn. Scaling uses every drawn price and
    the moving average, so the whole line stays inside the image.

    Returns (pixels uint8 [images, height, width] of 0/255, valid bool [images]). An image is
    invalid when nothing can be drawn or all its prices are equal."""
    b, n = close.shape
    if height is None:
        height, width = image_shape(n)
    else:                               # other window lengths (intraday charts): 3 pixels per bar
        width = 3 * n
    vol_h = height // 5 if volume_bars else 0
    price_h = height - vol_h
    out = np.zeros((b, height, width), np.uint8)

    bar_ok = np.isfinite(high) & np.isfinite(low)
    high, low = np.where(bar_ok, high, np.nan), np.where(bar_ok, low, np.nan)
    open_, close_tick = np.where(bar_ok, open_, np.nan), np.where(bar_ok, close, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)       # all-NaN rows are marked invalid below
        drawn = np.concatenate([high, low, open_, close_tick, ma], axis=1)
        lo, hi = np.nanmin(drawn, axis=1), np.nanmax(drawn, axis=1)
    valid = np.isfinite(lo) & np.isfinite(hi) & (hi > lo) & bar_ok.any(axis=1)
    lo, span = np.where(valid, lo, 0.0), np.where(valid, hi - lo, 1.0)

    # high-low bars in the centre column of each day
    top, bottom = _rows(high, lo, span, price_h), _rows(low, lo, span, price_h)
    r = np.arange(price_h)[None, :, None]
    bars = bar_ok[:, None, :] & (r >= top[:, None, :]) & (r <= bottom[:, None, :])
    out[:, :price_h, 1::3][bars] = 255

    def put(rows: np.ndarray, column: np.ndarray) -> None:
        i, d = np.nonzero(np.isfinite(rows))
        out[i, rows[i, d].astype(np.int64), column[d]] = 255

    days = np.arange(n)
    put(_rows(open_, lo, span, price_h), 3 * days)
    put(_rows(close_tick, lo, span, price_h), 3 * days + 2)

    # moving average: a dot in each centre column, joined to the next by an 8-connected line
    ma_rows = _rows(ma, lo, span, price_h)
    put(ma_rows, 3 * days + 1)
    i, d = np.nonzero(np.isfinite(ma_rows[:, :-1]) & np.isfinite(ma_rows[:, 1:]))
    if len(i):
        r0, r1 = ma_rows[i, d], ma_rows[i, d + 1]
        t = np.linspace(0.0, 1.0, price_h + 3)[None, :]     # more steps than pixels moved: no gaps
        rows = np.rint(r0[:, None] + t * (r1 - r0)[:, None]).astype(np.int64)
        cols = np.rint((3 * d + 1)[:, None] + 3 * t).astype(np.int64)
        out[np.broadcast_to(i[:, None], rows.shape), rows, cols] = 255

    if vol_h:
        v = np.where(np.isfinite(volume) & (volume >= 0), volume, np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            vmax = np.nanmax(v, axis=1)
        scale = np.where(np.isfinite(vmax) & (vmax > 0), vmax, np.inf)
        k = np.nan_to_num(np.rint(v / scale[:, None] * vol_h), nan=0.0)
        rv = np.arange(vol_h)[None, :, None]
        out[:, price_h:, 1::3][rv >= (vol_h - k)[:, None, :]] = 255

    out[~valid] = 0
    return out, valid


def pack(images: np.ndarray) -> np.ndarray:
    """0/255 images -> one bit per pixel (uint8 rows of height*width/8 bytes)."""
    return np.packbits(images.reshape(len(images), -1) > 0, axis=1)


def unpack(packed: np.ndarray, days: int) -> np.ndarray:
    h, w = image_shape(days)
    return np.unpackbits(packed, axis=1, count=h * w).reshape(-1, h, w) * np.uint8(255)


# ------------------------------------------------------------------------------------ panel

@dataclass
class Panel:
    """CRSP daily data as (trading days x stocks) matrices for one block of dates.

    `listed` is True where the stock has a CRSP row on an exchange in EXCHANGES. `total_ret` is
    the CRSP return with each delisting return placed on the first trading day after the
    stock's last row, so it falls in the holding period that contains the delisting."""
    dates: pd.DatetimeIndex
    permnos: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    ret: np.ndarray
    total_ret: np.ndarray
    mcap: np.ndarray
    listed: np.ndarray


def _positive(x: pd.Series) -> pd.Series:
    x = x.abs()              # CRSP marks bid/ask midpoints (no trade) with a negative sign
    return x.where(x > 0)


def panel_from_crsp(daily: pd.DataFrame, delist: pd.DataFrame | None = None,
                    share_codes: tuple[int, ...] | None = None) -> Panel:
    """Build a Panel from CRSP rows with columns permno, date, prc, openprc, askhi, bidlo, vol,
    ret, shrout, cfacshr, exchcd, shrcd; and the delisting table (permno, dlstdt, dlret)."""
    dates = pd.DatetimeIndex(np.sort(pd.to_datetime(daily["date"]).unique()))
    keep = daily["exchcd"].isin(EXCHANGES)
    if share_codes is not None:
        keep &= daily["shrcd"].isin(share_codes)
    d = daily.loc[keep].copy()
    d["date"] = pd.to_datetime(d["date"])
    d["close"] = _positive(d["prc"])
    d["open"] = _positive(d["openprc"])
    d["high"] = _positive(d["askhi"])
    d["low"] = _positive(d["bidlo"])
    d["volume"] = d["vol"].where(d["vol"] >= 0) * d["cfacshr"].where(d["cfacshr"] > 0).fillna(1.0)
    d["mcap"] = d["close"] * d["shrout"].where(d["shrout"] > 0)
    d["listed"] = 1.0
    d = d.drop_duplicates(["permno", "date"], keep="last")
    permnos = np.sort(d["permno"].unique())
    col = pd.Index(permnos)
    fields = ["open", "high", "low", "close", "volume", "ret", "mcap", "listed"]
    grid = d.set_index(["date", "permno"])[fields].unstack("permno").reindex(index=dates)

    def wide(field: str) -> np.ndarray:
        return grid[field].reindex(columns=col).to_numpy(np.float64)

    listed = wide("listed") == 1.0
    ret = wide("ret")
    total = np.where(listed, ret, np.nan)
    if delist is not None and len(delist):
        last_row = (d.groupby("permno")["date"].max())
        dl = delist.dropna(subset=["dlret"]).copy()
        dl["dlstdt"] = pd.to_datetime(dl["dlstdt"])
        dl = dl[dl["permno"].isin(last_row.index)]
        for permno, dlstdt, dlret in dl[["permno", "dlstdt", "dlret"]].itertuples(index=False):
            last = last_row[permno]
            if dlstdt < last or dlstdt > dates[-1]:
                continue                       # not a delisting inside this block of data
            pos = dates.searchsorted(last, side="right")
            if pos < len(dates):
                j = col.get_loc(permno)
                base = total[pos, j]
                total[pos, j] = (1 + (0.0 if np.isnan(base) else base)) * (1 + dlret) - 1
    return Panel(dates, permnos, wide("open"), wide("high"), wide("low"), wide("close"), wide("volume"),
                 ret, total, wide("mcap"), listed)


def formation_dates(dates: pd.DatetimeIndex, freq: str) -> pd.DatetimeIndex:
    """Last trading day of each week, month or quarter."""
    s = pd.Series(dates, index=dates)
    if freq == "week":
        key = dates.to_period("W-SUN")       # ISO week, Monday to Sunday
    elif freq == "month":
        key = dates.to_period("M")
    elif freq == "quarter":
        key = dates.to_period("Q")
    else:
        raise ValueError(freq)
    return pd.DatetimeIndex(s.groupby(key).max().to_numpy())


def _compound(rets: np.ndarray, axis: int = 0) -> np.ndarray:
    return np.prod(1 + np.nan_to_num(rets, nan=0.0), axis=axis) - 1


@dataclass
class Samples:
    meta: pd.DataFrame       # one row per image: date, permno, mcap, labels, benchmarks, hold_ret
    packed: np.ndarray       # packed images, aligned with meta


def build_samples(panel: Panel, days: int, formation: pd.DatetimeIndex, build: pd.DatetimeIndex | None = None,
                  volume_bars: bool = True, data_end: pd.Timestamp | None = None) -> Samples:
    """Images and labels for every stock with a full window ending at each formation date.

    `formation` is the whole rebalancing calendar (the holding return runs to the next date in
    it); images are built for the dates in `build` (default: all of them).

    A stock qualifies at t when it is listed on all `days` days of the window (no IPO or
    delisting inside it) and has a close and market cap at t. Missing prices inside the window
    are allowed and leave blank columns. Forward values that would need data after `data_end`
    (default: the panel's last date) are NaN, never zero."""
    n = days
    dates = panel.dates
    last_ok = len(dates) - 1 if data_end is None else dates.searchsorted(pd.Timestamp(data_end), side="right") - 1
    formation = formation[(formation >= dates[0]) & (formation <= dates[-1])]
    form_idx = dates.get_indexer(formation)
    if (form_idx < 0).any():
        raise ValueError("formation dates must be trading days in the panel")
    wanted = formation if build is None else formation.intersection(build)
    metas, packs = [], []
    for k, t in enumerate(form_idx):
        if dates[t] not in wanted:
            continue
        start = t - n + 1
        ext = t - 2 * n + 2                    # first day the moving average needs
        if ext < 0:
            continue
        ok = panel.listed[start:t + 1].all(axis=0) & np.isfinite(panel.close[t]) & np.isfinite(panel.mcap[t])
        cols = np.flatnonzero(ok)
        if not len(cols):
            continue
        # the close path from returns over the extended window, then the n-day moving average
        rets = np.where(panel.listed[ext:t + 1, cols], panel.ret[ext:t + 1, cols], np.nan)
        path = np.cumprod(1 + np.nan_to_num(rets, nan=0.0), axis=0)
        raw_close = panel.close[ext:t + 1, cols]
        close = np.where(np.isfinite(raw_close) & panel.listed[ext:t + 1, cols], path, np.nan)
        csum = np.concatenate([np.zeros((1, len(cols))), np.cumsum(np.nan_to_num(close), axis=0)])
        cnt = np.concatenate([np.zeros((1, len(cols))), np.cumsum(np.isfinite(close), axis=0)])
        hi_i = np.arange(n - 1, 2 * n - 1)     # image days inside the extended window
        with np.errstate(invalid="ignore", divide="ignore"):
            ma = (csum[hi_i + 1] - csum[hi_i + 1 - n]) / (cnt[hi_i + 1] - cnt[hi_i + 1 - n])
        img_close = close[n - 1:]
        level = img_close / raw_close[n - 1:]  # path level per unit of raw price, day by day
        o = panel.open[start:t + 1, cols] * level
        h = panel.high[start:t + 1, cols] * level
        lo = panel.low[start:t + 1, cols] * level
        vol = panel.volume[start:t + 1, cols]
        images, valid = draw(o.T, h.T, lo.T, img_close.T, ma.T, vol.T, volume_bars=volume_bars)
        cols, images = cols[valid], images[valid]
        if not len(cols):
            continue

        meta = pd.DataFrame({"date": dates[t], "permno": panel.permnos[cols], "mcap": panel.mcap[t, cols]})
        tr = panel.total_ret
        for hzn in HORIZONS:
            meta[f"ret_{hzn}d"] = _compound(tr[t + 1:t + 1 + hzn, cols]) if t + hzn <= last_ok else np.nan
        nxt = form_idx[k + 1] if k + 1 < len(form_idx) else -1
        meta["hold_ret"] = _compound(tr[t + 1:nxt + 1, cols]) if 0 <= nxt <= last_ok else np.nan
        # benchmark signals, oriented so that a higher score predicts a higher return
        meta["wstr"] = -_compound(tr[t - 4:t + 1, cols]) if t >= 4 else np.nan
        meta["str"] = -_compound(tr[t - 19:t + 1, cols]) if t >= 19 else np.nan
        if t >= MOM_FROM:
            mom = _compound(tr[t - MOM_FROM:t - MOM_TO + 1, cols])
            meta["mom"] = np.where(panel.listed[t - MOM_FROM, cols], mom, np.nan)
        else:
            meta["mom"] = np.nan
        metas.append(meta)
        packs.append(pack(images))
    if not metas:
        h_, w_ = image_shape(days)
        return Samples(pd.DataFrame(), np.zeros((0, h_ * w_ // 8), np.uint8))
    return Samples(pd.concat(metas, ignore_index=True), np.concatenate(packs))
