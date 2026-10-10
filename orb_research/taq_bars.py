"""TAQ five-minute bars for point-in-time S&P 500 members, 2012-2024 (idea B,
docs/INTRADAY_CHARTS_PLAN.md), read through the vault: 2021-2024 are sealed.

Columns: date, permno, sym_root, sym_suffix, source (link | ticker), b (bar 0..77, labelled by
its START, 09:30 + 5b), open, high, low, close, volume, trades, vwap. A bar's close is known at
its END (09:35 + 5b), never at its label.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import vault

BARS_DIR = Path("C:/data/taq_b/bars")
DATASET = "taq_bars5m"
FIRST_BAR_MINUTE = 570          # 09:30 New York
BAR_MINUTES = 5
N_BARS = 78


def bar_end_minute(b) -> int:
    """Minute of the New York day at which bar b's close becomes known."""
    return FIRST_BAR_MINUTE + BAR_MINUTES * (b + 1)


def load_bars(start, end, experiment: str | None = None, bars_dir: Path = BARS_DIR,
              columns: list[str] | None = None) -> pd.DataFrame:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    vault.check(DATASET, start, end, experiment)
    months = pd.period_range(start, end, freq="M")
    frames = []
    for m in months:
        f = Path(bars_dir) / f"bars_{m}.parquet"
        if f.exists():
            frames.append(pd.read_parquet(f, columns=columns))
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out[(out["date"] >= start) & (out["date"] <= end)].reset_index(drop=True)
