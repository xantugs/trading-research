"""Explicit UTC to strategy-clock conversion; naive output is intentional."""
from __future__ import annotations

import math
from zoneinfo import ZoneInfo

import pandas as pd


def to_server_time(index: pd.DatetimeIndex, tz: str) -> pd.DatetimeIndex:
    utc = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    if tz.lower() == "utc":
        return utc.tz_localize(None)
    if tz.lower().startswith("ny"):
        offset = float(tz[2:] or 0)
        local = utc.tz_convert(ZoneInfo("America/New_York")).tz_localize(None)
    else:
        offset = float(tz)
        local = utc.tz_localize(None)
    if not math.isfinite(offset) or abs(offset) > 24:
        raise ValueError("Timezone offset must be finite and within 24 hours")
    return local + pd.Timedelta(hours=offset)
