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


def load_ticks(source: str | Path | bytes, name: str | None = None,
               strategy_offset_hours: int = 0) -> pd.DataFrame:
    """Return standardized ticks, optionally shifted from source to strategy clock."""
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
        # TAQ's time_m carries microseconds and time_m_nano carries the remaining
        # three digits. Preserve both before ordering equal-microsecond events.
        if "time_m_nano" in raw:
            nanos = pd.to_numeric(raw.time_m_nano, errors="coerce").fillna(0)
            stamps = stamps + pd.to_timedelta(nanos, unit="ns")
    elif "date" in raw and "time" in raw:
        stamps = pd.to_datetime(raw.date.astype(str) + " " + raw.time.astype(str), errors="coerce")
    else:
        field = next((value for value in ("timestamp", "datetime", "time") if value in raw), None)
        if field is None:
            raise ValueError("Tick file needs time, timestamp, or date + time_m columns")
        stamps = pd.to_datetime(raw[field], errors="coerce")
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_localize(None)
    if strategy_offset_hours:
        stamps = stamps + pd.Timedelta(hours=strategy_offset_hours)

    prices = [column for column in ("bid", "ask", "price") if column in raw]
    if not prices:
        raise ValueError("Tick file needs bid/ask quotes or a price column")
    output = raw.drop(columns=[column for column in ("timestamp", "datetime", "time", "date", "time_m")
                               if column in raw], errors="ignore").copy()
    output["_stamp"] = stamps
    dropped_timestamps = int(output._stamp.isna().sum())
    output = output[output._stamp.notna()]
    sequence = next((column for column in ("tr_seqnum", "qu_seqnum", "seqnum") if column in output), None)
    ordering = ["_stamp"] + ([sequence] if sequence else [])
    output = output.sort_values(ordering, kind="stable").set_index("_stamp")
    output.index.name = "time"
    for column in prices:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    keep = prices + [column for column in output if column not in prices]
    output = output[keep]
    output.attrs["dropped_timestamps"] = dropped_timestamps
    return output


def tick_quality(ticks: pd.DataFrame) -> dict[str, int | float | str]:
    """Small, deterministic audit used by the Streamlit tick overlay."""
    price_columns = [column for column in ("bid", "ask", "price") if column in ticks]
    numeric = ticks[price_columns].apply(pd.to_numeric, errors="coerce")
    matrix = numeric.to_numpy(dtype=float, na_value=np.nan)
    invalid = ~np.isfinite(matrix).all(axis=1) | (matrix <= 0).any(axis=1)
    crossed = (((pd.to_numeric(ticks.ask, errors="coerce")
                 < pd.to_numeric(ticks.bid, errors="coerce")).fillna(False))
               if {"bid", "ask"}.issubset(ticks.columns)
               else pd.Series(False, index=ticks.index))
    return {
        "rows": len(ticks),
        "first": str(ticks.index.min()),
        "last": str(ticks.index.max()),
        "duplicate_timestamps": int(ticks.index.duplicated(keep=False).sum()),
        "dropped_timestamps": int(ticks.attrs.get("dropped_timestamps", 0)),
        "invalid_prices": int(invalid.sum()),
        "crossed_quotes": int(crossed.sum()),
    }
