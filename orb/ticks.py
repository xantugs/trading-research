"""Load synchronized quote or trade ticks for visual inspection and future replay.

WRDS TAQ daily files use ``date`` + ``time_m`` with ``best_bid`` and
``best_ask``. Compact research files may instead use ``time,bid,ask`` or
``time,price``. Timestamps remain naive and retain the source clock.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd


def _read(source: str | Path | bytes, name: str | None) -> pd.DataFrame:
    hint = (name or str(source) if not isinstance(source, bytes) else name or "").lower()
    payload = BytesIO(source) if isinstance(source, bytes) else source
    if hint.endswith((".parquet", ".pq")):
        return pd.read_parquet(payload)
    return pd.read_csv(payload, compression="gzip" if hint.endswith(".gz") else "infer")


def load_ticks(source: str | Path | bytes, name: str | None = None) -> pd.DataFrame:
    """Return a time-indexed frame with standardized ``bid``, ``ask`` and/or ``price``."""
    raw = _read(source, name)
    if raw.empty:
        raise ValueError("Tick file is empty")
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    raw = raw.rename(columns={
        "best_bid": "bid", "best_ask": "ask", "trade_price": "price",
        "price_m": "price", "size_m": "size",
    })

    if "date" in raw and "time_m" in raw:
        stamps = pd.to_datetime(raw.date.astype(str) + " " + raw.time_m.astype(str), errors="coerce")
    elif "date" in raw and "time" in raw:
        stamps = pd.to_datetime(raw.date.astype(str) + " " + raw.time.astype(str), errors="coerce")
    else:
        field = next((value for value in ("timestamp", "datetime", "time") if value in raw), None)
        if field is None:
            raise ValueError("Tick file needs time, timestamp, or date + time_m columns")
        stamps = pd.to_datetime(raw[field], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_localize(None)

    prices = [column for column in ("bid", "ask", "price") if column in raw]
    if not prices:
        raise ValueError("Tick file needs bid/ask quotes or a price column")
    output = raw.drop(columns=[column for column in ("timestamp", "datetime", "time", "date", "time_m")
                               if column in raw], errors="ignore").copy()
    output.index = pd.DatetimeIndex(stamps, name="time")
    output = output[~output.index.isna()].sort_index(kind="stable")
    for column in prices:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    keep = prices + [column for column in output if column not in prices]
    return output[keep]


def tick_quality(ticks: pd.DataFrame) -> dict[str, int | float | str]:
    """Small, deterministic audit used by the Streamlit tick overlay."""
    price_columns = [column for column in ("bid", "ask", "price") if column in ticks]
    invalid = ~np.isfinite(ticks[price_columns]).all(axis=1) | (ticks[price_columns] <= 0).any(axis=1)
    crossed = ((ticks.ask < ticks.bid) if {"bid", "ask"}.issubset(ticks.columns)
               else pd.Series(False, index=ticks.index))
    return {
        "rows": len(ticks),
        "first": str(ticks.index.min()),
        "last": str(ticks.index.max()),
        "duplicate_timestamps": int(ticks.index.duplicated(keep=False).sum()),
        "invalid_prices": int(invalid.sum()),
        "crossed_quotes": int(crossed.sum()),
    }
