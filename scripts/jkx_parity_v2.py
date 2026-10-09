"""Gate-3 parity check (docs/JKX_REPLICATION_PLAN.md): does CRSP's new format, mapped by
orb_research.crsp_v2, reproduce the legacy file on the open year 2019?

    .venv/Scripts/python -m scripts.jkx_parity_v2

Pass: every compared field agrees on >= 99.9% of stock-days present in both, and the 2019
weekly WSTR/R5 and STR/R5 benchmark H-L Sharpe ratios (equal and value weight) agree to two
decimals. (12-month momentum needs 2018 in the new format and is not compared.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from orb_research import crsp, crsp_v2
from orb_research.jkx_images import build_samples, formation_dates, panel_from_crsp
from orb_research.jkx_portfolio import decile_returns, summarize

FIELDS = ("prc", "openprc", "askhi", "bidlo", "ret", "vol", "shrout")


def agree(a: pd.Series, b: pd.Series) -> float:
    a, b = a.astype(float), b.astype(float)
    both_nan = a.isna() & b.isna()
    close = np.isclose(a.abs(), b.abs(), rtol=1e-4, atol=1e-6)
    return float((both_nan | close).mean())


def benchmarks(daily: pd.DataFrame, delist: pd.DataFrame) -> dict:
    panel = panel_from_crsp(daily, delist)
    form = formation_dates(panel.dates, "week")
    form = form[form < panel.dates[-1]]
    s = build_samples(panel, 5, form, build=form[form >= pd.Timestamp("2019-02-01")])
    out = {}
    for sig in ("wstr", "str"):
        for w in (None, "mcap"):
            hl = decile_returns(s.meta, sig, weight=w)["H-L"]
            out[f"{sig}|{'vw' if w else 'ew'}"] = round(summarize(hl, "week")["sharpe"], 2)
    return out


def main() -> None:
    legacy = crsp.load_daily("2019-01-01", "2019-12-31")
    legacy_delist = crsp.load_delist("2019-12-31")
    v2_delist = crsp_v2.delist_to_legacy(pd.read_parquet(crsp.DEFAULT_DIR / "v2" / "stkdelists.parquet"))
    v2_delist = v2_delist[v2_delist["dlstdt"] <= "2019-12-31"]
    ok = True
    for active_only in (True, False):
        v2 = crsp_v2.load_year(2019, active_only=active_only)
        lu = legacy[legacy["exchcd"].isin((1, 2, 3))]
        vu = v2[v2["exchcd"].isin((1, 2, 3))]
        keys_l = set(zip(lu["permno"], lu["date"]))
        keys_v = set(zip(vu["permno"], vu["date"]))
        print(f"\nmapping active_only={active_only}: universe stock-days legacy {len(keys_l):,}, new {len(keys_v):,}, "
              f"both {len(keys_l & keys_v):,}, legacy-only {len(keys_l - keys_v):,}, new-only {len(keys_v - keys_l):,}")
        m = lu.merge(vu, on=["permno", "date"], suffixes=("_l", "_v"))
        rates = {f: agree(m[f + "_l"], m[f + "_v"]) for f in FIELDS}
        print("field agreement on common stock-days:", {k: round(v, 5) for k, v in rates.items()})
        universe_rate = len(keys_l & keys_v) / max(len(keys_l | keys_v), 1)
        print(f"universe agreement (both / either): {universe_rate:.5f}")
        if active_only:
            primary_ok = all(r >= 0.999 for r in rates.values()) and universe_rate >= 0.999
    b_legacy = benchmarks(legacy, legacy_delist)
    b_v2 = benchmarks(crsp_v2.load_year(2019, active_only=True), v2_delist)
    print("\n2019 benchmark H-L Sharpe, legacy:", b_legacy)
    print("2019 benchmark H-L Sharpe, new   :", b_v2)
    ok = primary_ok and b_legacy == b_v2
    print("\nPARITY", "PASSED" if ok else "FAILED")


if __name__ == "__main__":
    main()
