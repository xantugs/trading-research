"""Feasibility probe for idea B (intraday chart images): which TAQ tables are reachable on WRDS,
and how long one day of server-side 5-minute bars takes. Reads nothing beyond a few sample days.

    .venv/Scripts/python -m scripts.taq_probe --user <wrds user>
"""
from __future__ import annotations

import argparse
import time

from orb_research.crsp import connect, query

BARS_SQL = """
WITH t AS (
  SELECT sym_root, time_m, price, size,
         floor(extract(epoch FROM time_m) / 300)::int AS b
  FROM {table}
  WHERE sym_root = ANY(%(syms)s) AND (sym_suffix IS NULL OR sym_suffix = '')
    AND tr_corr = '00' AND price > 0
    AND time_m >= '09:30:00' AND time_m < '16:00:00'
)
SELECT sym_root, b,
       (array_agg(price ORDER BY time_m))[1] AS open,
       max(price) AS high, min(price) AS low,
       (array_agg(price ORDER BY time_m DESC))[1] AS close,
       sum(size) AS volume, count(*) AS trades
FROM t GROUP BY sym_root, b
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    a = p.parse_args()
    t0 = time.time()
    conn = connect(a.user)
    print(f"connected ({time.time() - t0:.0f}s)", flush=True)
    try:
        sch = query(conn, "SELECT schema_name FROM information_schema.schemata "
                          "WHERE schema_name LIKE 'taq%%' ORDER BY 1")
        print("TAQ schemas:", ", ".join(sch["schema_name"]), flush=True)
        for schema, day in (("taqm_2019", "20190102"), ("taqm_2010", "20100104"), ("taqm_2024", "20240102")):
            table = f"{schema}.ctm_{day}"
            try:
                cols = query(conn, f"SELECT * FROM {table} LIMIT 1")
            except Exception as exc:                       # noqa: BLE001
                conn.rollback()
                print(f"{table}: not available ({str(exc).splitlines()[0]})", flush=True)
                continue
            print(f"{table}: columns {', '.join(cols.columns)}", flush=True)
            for n, syms in ((5, ["AAPL", "MSFT", "JPM", "XOM", "GE"]), (100, None)):
                if syms is None:
                    syms = list(query(conn, f"SELECT sym_root FROM {table} WHERE tr_corr = '00' "
                                            "GROUP BY sym_root ORDER BY count(*) DESC LIMIT 100")["sym_root"])
                t = time.time()
                bars = query(conn, BARS_SQL.format(table=table), {"syms": syms})
                print(f"  {n} symbols: {len(bars):,} five-minute bars in {time.time() - t:.1f}s", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
