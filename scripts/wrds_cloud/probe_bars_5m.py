#!/usr/bin/env python3
"""Run ON WRDS Cloud (python3 probe_bars_5m.py). Feasibility for idea B
(docs/INTRADAY_CHARTS_PLAN.md):
- the TAQ millisecond date range in taqmsec;
- the CRSP-TAQ link tables;
- how long one day of server-side 5-minute bars takes for the 500 most-traded symbols.
"""
from __future__ import annotations

import getpass
import time

import wrds

BARS = """
select sym_root,
       floor(extract(epoch from time_m) / 300)::int as b,
       (array_agg(price order by time_m))[1] as open, max(price) as high, min(price) as low,
       (array_agg(price order by time_m desc))[1] as close, sum(size) as volume, count(*) as trades
from taqmsec.{t}
where sym_root = any(%(syms)s) and coalesce(sym_suffix, '') = ''
  and tr_corr = '00' and price > 0 and size > 0
  and time_m >= '09:30:00' and time_m < '16:00:00'
group by 1, 2
"""


def main() -> None:
    db = wrds.Connection(wrds_username=getpass.getuser())
    t = db.raw_sql("select min(table_name) a, max(table_name) b, count(*) n from information_schema.tables "
                   "where table_schema = 'taqmsec' and table_name like 'ctm\\_%%'")
    print("taqmsec trade tables:", t.to_dict("records"), flush=True)
    links = db.raw_sql("select table_schema, table_name from information_schema.tables "
                       "where table_name ilike '%%taq%%' and table_schema ilike 'wrdsapps%%' order by 1, 2")
    print("CRSP-TAQ link tables:", links.to_dict("records"), flush=True)
    for day in ("20050103", "20190102", "20240102"):
        tab = f"ctm_{day}"
        try:
            t0 = time.time()
            top = db.raw_sql(f"select sym_root from taqmsec.{tab} where tr_corr = '00' and coalesce(sym_suffix, '') = '' "
                             "group by 1 order by count(*) desc limit 500")
            t1 = time.time()
            bars = db.raw_sql(BARS.format(t=tab), params={"syms": list(top["sym_root"])})
            t2 = time.time()
            print(f"{day}: top-500 list {t1 - t0:.0f}s; 5-minute bars {len(bars):,} rows "
                  f"for {bars['sym_root'].nunique()} symbols in {t2 - t1:.0f}s", flush=True)
        except Exception as exc:                                # noqa: BLE001
            print(f"{day}: {str(exc).splitlines()[0]}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
