"""CRSP's new (CIZ) daily stock data mapped onto the legacy columns used by orb_research.jkx_images,
so the 2025 extension of Gate 3 uses the same code path (docs/JKX_REPLICATION_PLAN.md). The mapping is
checked against the legacy file on the open year 2019 by scripts/jkx_parity_v2.py before 2025 is used.

Inputs (scripts/download_crsp_v2.py): crsp.stkdlysecuritydata (daily prices), crsp.stksecurityinfohist
(exchange, trading status by date range) and crsp.stkdelists.
- Exchange: primaryexch N/A/Q -> exchcd 1/2/3 for the info row whose date range covers the day.
- Shares outstanding: dlycap / |dlyprc| (only relative sizes matter: value weights, the 500 largest).
- Share-adjustment factor for volume: the new tables carry no cumulative factor, so splits are read
  from the gap between the ex-dividend return and the raw price change, f_t = (1 + retx_t) p_{t-1} / p_t,
  and cfacshr is the product of f over all later days (shares expressed in end-of-file units).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .crsp import DEFAULT_DIR

EXCHANGE = {"N": 1, "A": 2, "Q": 3}


def _with_info(daily: pd.DataFrame, info: pd.DataFrame) -> pd.DataFrame:
    """Attach the security-info row whose [secinfostartdt, secinfoenddt] covers each day."""
    d = daily.sort_values("dlycaldt").reset_index(drop=True)
    i = info.dropna(subset=["secinfostartdt"]).sort_values("secinfostartdt")
    m = pd.merge_asof(d, i, left_on="dlycaldt", right_on="secinfostartdt", by="permno", direction="backward")
    stale = m["secinfoenddt"].notna() & (m["dlycaldt"] > m["secinfoenddt"])
    m.loc[stale, ["primaryexch", "tradingstatusflg"]] = np.nan
    return m


def _share_factor(d: pd.DataFrame) -> pd.Series:
    d = d.sort_values(["permno", "dlycaldt"])
    prc = d["dlyprc"].abs()
    prev = prc.groupby(d["permno"]).shift(1)
    f = (1 + d["dlyretx"]) * prev / prc
    f = f.where(np.isfinite(f) & ((f - 1).abs() > 0.02), 1.0)      # only splits / share distributions
    # product of f over all LATER days of the same stock
    later = f.groupby(d["permno"]).transform(lambda s: s[::-1].cumprod()[::-1].shift(-1).fillna(1.0))
    return later.reindex(d.index)


def to_legacy(daily: pd.DataFrame, info: pd.DataFrame, active_only: bool = True) -> pd.DataFrame:
    m = _with_info(daily, info)
    exch = m["primaryexch"].map(EXCHANGE)
    if active_only:
        exch = exch.where(m["tradingstatusflg"] == "A")
    prc = m["dlyprc"]
    return pd.DataFrame({
        "permno": m["permno"].astype("int32"), "date": pd.to_datetime(m["dlycaldt"]),
        "prc": prc, "openprc": m["dlyopen"], "askhi": m["dlyhigh"], "bidlo": m["dlylow"],
        "vol": m["dlyvol"], "ret": m["dlyret"], "shrout": m["dlycap"] / prc.abs(),
        "cfacpr": np.nan, "cfacshr": _share_factor(m).to_numpy(),
        "exchcd": exch.astype("Int16"), "shrcd": pd.Series(pd.NA, index=m.index, dtype="Int16"),
    })


def delist_to_legacy(d: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"permno": d["permno"].astype("int32"), "dlstdt": pd.to_datetime(d["delistingdt"]),
                         "dlret": pd.to_numeric(d["delret"], errors="coerce")})


def load_year(year: int, crsp_dir: Path = DEFAULT_DIR, active_only: bool = True) -> pd.DataFrame:
    base = Path(crsp_dir) / "v2"
    return to_legacy(pd.read_parquet(base / f"stkdly_{year}.parquet"),
                     pd.read_parquet(base / "stksecurityinfohist.parquet"), active_only)
