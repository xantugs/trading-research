"""Loading and reshaping price data for the ORB backtester.

Accepted inputs (all in BROKER SERVER TIME, exactly like MetaTrader):

* CSV written by ``fetch_mt5.py``:
      time,open,high,low,close,tick_volume,spread,real_volume
* MetaTrader's own export (View -> Symbols -> Bars -> Export), tab separated:
      <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>
* Any CSV with a ``time``/``datetime``/``date`` column and open/high/low/close.

Everything is normalised to a DataFrame indexed by a naive DatetimeIndex called
``time`` with the columns ``open, high, low, close, tick_volume, spread``
(``spread`` is in points, as MT5 reports it, and may be all zeros).
"""
from __future__ import annotations

from pathlib import Path
import io

import numpy as np
import pandas as pd

COLUMNS = ["open", "high", "low", "close", "tick_volume", "spread"]


def load_bars(path: str | Path | bytes) -> pd.DataFrame:
    """Read a bar file in any of the supported layouts."""
    if isinstance(path, bytes):
        path = io.BytesIO(path)
        head = path.readline().decode("utf-8-sig")
        path.seek(0)
    else:
        path = Path(path)
        with path.open("r", encoding="utf-8-sig") as handle:
            head = handle.readline()

    if head.startswith("<DATE>"):
        raw = pd.read_csv(path, sep="\t", encoding="utf-8-sig")
        raw.columns = [c.strip("<>").lower() for c in raw.columns]
        raw["time"] = pd.to_datetime(raw["date"] + " " + raw["time"], format="%Y.%m.%d %H:%M:%S")
        raw = raw.rename(columns={"tickvol": "tick_volume", "vol": "real_volume"})
    else:
        sep = "\t" if "\t" in head else ","
        raw = pd.read_csv(path, sep=sep, encoding="utf-8-sig", comment="#")
        raw.columns = [c.strip().lower() for c in raw.columns]
        tcol = next((c for c in ("time", "datetime", "date") if c in raw.columns), None)
        if tcol is None:
            raise ValueError(f"{path}: no time/datetime column found (columns: {list(raw.columns)})")
        raw["time"] = pd.to_datetime(raw[tcol])

    return normalise(raw)


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Turn a loosely-shaped frame into the canonical bar frame."""
    df = raw.copy()
    if "tick_volume" not in df.columns:
        df["tick_volume"] = df["volume"] if "volume" in df.columns else 0
    if "spread" not in df.columns:
        df["spread"] = 0
    if not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index("time")
    if df.index.tz is not None:
        raise ValueError("Bars must use naive strategy-clock times; convert UTC explicitly before loading")
    df.index.name = "time"
    missing = set(COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {sorted(missing)}")
    df = df[COLUMNS].astype(float)
    validate_bars(df)
    df = df.sort_index()
    return df


def validate_bars(df: pd.DataFrame) -> None:
    if df.empty:
        raise ValueError("Bar data is empty")
    if not isinstance(df.index, pd.DatetimeIndex) or df.index.hasnans or df.index.tz is not None:
        raise ValueError("Expected valid naive strategy-clock timestamps")
    if df.index.has_duplicates:
        raise ValueError("Duplicate bar timestamps; resolve source duplicates or DST ambiguity explicitly")
    price = df[["open", "high", "low", "close"]]
    if not np.isfinite(price.to_numpy()).all() or (price <= 0).any().any():
        raise ValueError("OHLC must be finite and positive")
    if (df.high < price.max(axis=1)).any() or (df.low > price.min(axis=1)).any():
        raise ValueError("Invalid OHLC envelope")
    for col in ("tick_volume", "spread"):
        if col in df and (not np.isfinite(df[col]).all() or (df[col] < 0).any()):
            raise ValueError(f"{col} must be finite and nonnegative")


def infer_minutes(bars: pd.DataFrame) -> int:
    """Most common bar spacing, in minutes."""
    diffs = np.diff(bars.index.values).astype("timedelta64[s]").astype(np.int64)
    diffs = diffs[diffs > 0]
    if len(diffs) == 0:
        raise ValueError("need at least two bars to infer the timeframe")
    values, counts = np.unique(diffs, return_counts=True)
    seconds = int(values[np.argmax(counts)])
    if seconds < 60 or seconds % 60:
        raise ValueError("Expected minute bars, not ticks or sub-minute data")
    return seconds // 60


def resample(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Build higher-timeframe bars the way MT5 does: bar time = open time, bins aligned to midnight."""
    if not isinstance(minutes, int) or minutes <= 0 or 1440 % minutes:
        raise ValueError("Timeframe must be a positive integer divisor of 1440 minutes")
    rule = f"{minutes}min"
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "tick_volume": "sum",
        "spread": "max",
    }
    agg = {key: value for key, value in agg.items() if key in bars.columns}
    out = bars.resample(rule, label="left", closed="left", origin="start_day").agg(agg)
    out = out.dropna(subset=["open"])
    out.index.name = "time"
    return out


def daily_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Server-time daily bars (what MT5's D1 chart shows)."""
    return resample(bars, 24 * 60)


def wilder_atr(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range with Wilder smoothing (not MT5's simple ATR).

    The value at day D is the ATR *including* day D, so use ``.shift(1)`` for the
    "yesterday's ATR" the EA reads (it copies buffer index 1).
    """
    if period <= 0:
        raise ValueError("ATR period must be positive")
    if daily.empty:
        return pd.Series(dtype=float, index=daily.index, name="atr")
    high, low, close = daily["high"].values, daily["low"].values, daily["close"].values
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]

    atr = np.full(len(tr), np.nan)
    if len(tr) >= period:
        atr[period - 1] = tr[:period].mean()
        for i in range(period, len(tr)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return pd.Series(atr, index=daily.index, name="atr")


def moving_average(closes: pd.Series, period: int, method: str = "ema") -> pd.Series:
    """EMA / SMA on completed bars, MT5 style (EMA seeded with the first close)."""
    if method == "sma":
        return closes.rolling(period).mean()
    alpha = 2.0 / (period + 1)
    return closes.ewm(alpha=alpha, adjust=False).mean()


def mt5_atr(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """Simple rolling true range, seeded after period+1 bars (MetaQuotes ATR.mq5)."""
    if period <= 0:
        raise ValueError("ATR period must be positive")
    previous = daily.close.shift(1)
    tr = pd.concat([daily.high, previous], axis=1).max(axis=1) - pd.concat(
        [daily.low, previous], axis=1).min(axis=1)
    if len(tr):
        tr.iloc[0] = np.nan
    return tr.rolling(period, min_periods=period).mean().rename("atr")
