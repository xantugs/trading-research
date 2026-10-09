#!/usr/bin/env python3
"""Run ON WRDS Cloud. Point-in-time S&P 500 five-minute bars from TAQ millisecond trades, one
Parquet file per month (idea B, docs/INTRADAY_CHARTS_PLAN.md).

For each trading day d:
- members are the CRSP permnos in crsp.dsp500list with start <= d <= ending;
- their TAQ symbols come from wrdsapps_link_crsp_taqm.taqmclink on date d (best match_lvl). Members
  the link misses (about 15% in 2019, mostly foreign-incorporated) fall back to the CRSP ticker on
  date d (class suffix only for BRK and BF). The `source` column records which; Gate D checks every
  stock-day's last bar against the CRSP close, which catches wrong matches;
- bars are built inside the database. Regular-session trades 09:30:00 <= time < 16:00:00,
  tr_corr '00', price and size > 0. Bucket b = 0..77 covers [09:30 + 5b, 09:35 + 5b);
  a bar is labelled by its START.

    python3 taq_bars_5m.py --month 2019-01 --out /scratch/cuhkcn/hantu/intraday_b/bars
"""
from __future__ import annotations

import argparse
import getpass
import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import wrds

MEMBERS = """
select l.permno, l.sym_root, coalesce(l.sym_suffix, '') as sym_suffix, l.match_lvl
from wrdsapps_link_crsp_taqm.taqmclink l
join crsp.dsp500list s on s.permno = l.permno and %(d)s between s.start and s.ending
where l.date = %(d)s
"""

MEMBERS_TICKER = """
select s.permno, n.ticker as sym_root, case when n.ticker in ('BRK', 'BF') then coalesce(n.shrcls, '') else '' end
       as sym_suffix
from crsp.dsp500list s
join crsp.stocknames n on n.permno = s.permno and %(d)s between n.namedt and n.nameenddt
where %(d)s between s.start and s.ending
"""

BARS = """
select sym_root, coalesce(sym_suffix, '') as sym_suffix,
       floor((extract(epoch from time_m) - 34200) / 300)::int as b,
       (array_agg(price order by time_m))[1] as open, max(price) as high, min(price) as low,
       (array_agg(price order by time_m desc))[1] as close,
       sum(size) as volume, count(*) as trades, sum(price * size) / sum(size) as vwap
from taqmsec.{t}
where sym_root = any(%(roots)s)
  and tr_corr = '00' and price > 0 and size > 0
  and time_m >= '09:30:00' and time_m < '16:00:00'
group by 1, 2, 3
"""


def month_days(month: str):
    first = date.fromisoformat(month + "-01")
    d = first
    while d.month == first.month:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--month", required=True, help="YYYY-MM")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--days", type=int, default=0, help="debug: only the first N trading days")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    target = a.out / f"bars_{a.month}.parquet"
    if target.exists():
        print(f"{target} exists; skipped", flush=True)
        return
    db = wrds.Connection(wrds_username=getpass.getuser())
    have = set(db.raw_sql("select table_name from information_schema.tables where table_schema = 'taqmsec' "
                          "and table_name like %(p)s", params={"p": "ctm_" + a.month.replace("-", "") + "%%"})
               ["table_name"])
    frames, log, started = [], [], time.time()
    days = [d for d in month_days(a.month) if f"ctm_{d:%Y%m%d}" in have]
    if a.days:
        days = days[: a.days]
    for d in days:
        t0 = time.time()
        mem = db.raw_sql(MEMBERS, params={"d": d})
        mem = mem.sort_values("match_lvl").drop_duplicates("permno").assign(source="link")
        tick = db.raw_sql(MEMBERS_TICKER, params={"d": d})
        tick = tick[~tick["permno"].isin(mem["permno"])].dropna(subset=["sym_root"]).assign(source="ticker")
        mem = pd.concat([mem, tick], ignore_index=True)
        bars = db.raw_sql(BARS.format(t=f"ctm_{d:%Y%m%d}"), params={"roots": sorted(set(mem["sym_root"]))})
        bars = bars.merge(mem[["permno", "sym_root", "sym_suffix", "source"]], on=["sym_root", "sym_suffix"], how="inner")
        bars.insert(0, "date", pd.Timestamp(d))
        frames.append(bars)
        log.append({"date": str(d), "members": int(mem["permno"].nunique()), "with_bars": int(bars["permno"].nunique()),
                    "by_ticker": int((mem["source"] == "ticker").sum()),
                    "rows": len(bars), "seconds": round(time.time() - t0, 1)})
        print(f"{d}: {log[-1]['with_bars']}/{log[-1]['members']} members, {len(bars):,} bars, "
              f"{log[-1]['seconds']}s", flush=True)
    db.close()
    if frames:
        out = pd.concat(frames, ignore_index=True)
        out = out.astype({"permno": "int32", "b": "int16", "volume": "int64", "trades": "int32"})
        tmp = target.with_suffix(".tmp")
        out.to_parquet(tmp, index=False, compression="zstd")
        tmp.replace(target)
    (a.out / f"log_{a.month}.json").write_text(json.dumps({"month": a.month, "days": log,
                                                          "seconds": round(time.time() - started)}, indent=1))
    print(f"done {a.month}: {len(days)} days in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
