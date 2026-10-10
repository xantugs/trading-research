"""Gate D (docs/INTRADAY_CHARTS_PLAN.md): do the TAQ 5-minute bars agree with CRSP, and is the
clock New York time?

    .venv/Scripts/python -m scripts.taq_gate_d

For every stock-day, 2012-2024:
- price: the last bar's close (the last regular-session trade before 16:00) against the CRSP close.
  The CRSP close is usually the closing-auction price at 16:00, so small gaps are expected.
- volume: the sum of the day's bars against CRSP daily volume. The bars exclude the 16:00
  auction and off-hours trades, so the ratio is expected below 1.
- clock: mean |5-minute return| and mean volume by bar of the day; the open bar should lead.
Reads only the open years (2012-2020 by default) unless --all; 2021-2024 are kept for the
sealed test. Bars are joined to CRSP through the PERMNO recorded at extraction.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from orb_research import crsp

BARS = Path("C:/data/taq_b/bars")


def year_check(year: int) -> tuple[dict, pd.DataFrame]:
    bars = pd.concat([pd.read_parquet(f) for f in sorted(BARS.glob(f"bars_{year}-*.parquet"))], ignore_index=True)
    bars["date"] = pd.to_datetime(bars["date"])
    last = bars.sort_values("b").groupby(["date", "permno"]).tail(1)
    day = bars.groupby(["date", "permno"]).agg(volume=("volume", "sum"), nbars=("b", "size")).reset_index()
    c = crsp.load_daily(f"{year}-01-01", f"{year}-12-31")[["permno", "date", "prc", "vol"]]
    m = last[["date", "permno", "b", "close"]].merge(day, on=["date", "permno"]).merge(c, on=["date", "permno"], how="left")
    gap = (m["close"] / m["prc"].abs() - 1).abs()
    ratio = m["volume"] / m["vol"]
    # clock: mean |return| and volume by bar of the day (returns within a stock-day)
    b = bars.sort_values(["permno", "date", "b"])
    b["r"] = b.groupby(["permno", "date"])["close"].pct_change().abs()
    by_bar = b.groupby("b").agg(abs_ret=("r", "mean"), volume=("volume", "mean"))
    out = {"year": year, "stock_days": len(m), "crsp_matched": float(m["prc"].notna().mean()),
           "close_within_0.5%": float((gap <= 0.005).mean()), "close_within_0.1%": float((gap <= 0.001).mean()),
           "median_gap_bps": float(gap.median() * 1e4), "last_bar_is_15:55": float((m["b"] == 77).mean()),
           "full_78_bars": float((m["nbars"] == 78).mean()),
           "volume_ratio_median": float(ratio.median()), "volume_ratio_p05": float(ratio.quantile(0.05)),
           "open_bar_rank_absret": int(by_bar["abs_ret"].rank(ascending=False).loc[1]),
           "open_bar_rank_volume": int(by_bar["volume"].rank(ascending=False).loc[0])}
    worst = m.assign(gap=gap).sort_values("gap", ascending=False).head(5)[["date", "permno", "close", "prc", "gap"]]
    return out, worst


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--first", type=int, default=2012)
    p.add_argument("--last", type=int, default=2020)
    a = p.parse_args()
    rows = []
    for y in range(a.first, a.last + 1):
        r, worst = year_check(y)
        rows.append(r)
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
        if r["close_within_0.5%"] < 0.99:
            print(worst.to_string(), flush=True)
    t = pd.DataFrame(rows)
    out = Path("outputs/taq_b")
    out.mkdir(parents=True, exist_ok=True)
    t.to_csv(out / "gate_d.csv", index=False)
    ok = (t["close_within_0.5%"] >= 0.99).all() and (t["crsp_matched"] >= 0.99).all()
    print("\nGATE D price check:", "PASS" if ok else "FAIL",
          f"(worst year: {t['close_within_0.5%'].min():.4f} within 0.5%)")


if __name__ == "__main__":
    main()
