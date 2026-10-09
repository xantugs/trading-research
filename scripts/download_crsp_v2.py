"""Download CRSP's new (CIZ) daily stock data for the given years, raw, over ONE connection (one Duo
approval). Used by the Gate-3 parity check and the 2025 extension (docs/JKX_REPLICATION_PLAN.md).

    .venv/Scripts/python -m scripts.download_crsp_v2 --user <wrds user> --years 2019 2025

The convenience view crsp.dsf_v2 took over 20 minutes for one month, so this reads the base tables:
crsp.stkdlysecuritydata (daily prices) and crsp.stksecurityinfohist (exchange, trading status,
share type, by date range), and crsp.stkdelists. The column list of each table is read first and
only the wanted columns that exist are selected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from orb_research.crsp import DEFAULT_DIR, connect, query

DAILY_WANTED = ("permno", "dlycaldt", "dlyprc", "dlyprcflg", "dlyopen", "dlyhigh", "dlylow", "dlyclose", "dlybid",
                "dlyask", "dlyret", "dlyretx", "dlyretmissflg", "dlyvol", "dlycumfacpr", "dlycumfacshr", "shrout",
                "dlycap", "dlydelflg")
INFO_WANTED = ("permno", "secinfostartdt", "secinfoenddt", "primaryexch", "tradingstatusflg", "sharetype",
               "securitytype", "securitysubtype", "conditionaltype", "issuertype", "usincflg")
TEXT = {"primaryexch", "tradingstatusflg", "sharetype", "securitytype", "securitysubtype", "conditionaltype",
        "issuertype", "usincflg", "dlyprcflg", "dlyretmissflg", "dlydelflg", "delactiontype", "delstatustype",
        "delreasontype"}


def columns(conn, table: str) -> list[str]:
    return list(query(conn, f"SELECT * FROM {table} LIMIT 1").columns)


def tidy(frame: pd.DataFrame) -> pd.DataFrame:
    for c in frame.columns:
        if c.endswith("dt"):
            frame[c] = pd.to_datetime(frame[c], errors="coerce")
        elif c not in TEXT and frame[c].dtype == object:
            frame[c] = pd.to_numeric(frame[c], errors="coerce")
    return frame


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    p.add_argument("--years", type=int, nargs="+", required=True)
    p.add_argument("--out", type=Path, default=DEFAULT_DIR / "v2")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    manifest_path = a.out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    conn = connect(a.user)
    print("connected", flush=True)
    try:
        daily_cols = [c for c in DAILY_WANTED if c in columns(conn, "crsp.stkdlysecuritydata")]
        info_cols = [c for c in INFO_WANTED if c in columns(conn, "crsp.stksecurityinfohist")]
        print("daily columns:", ", ".join(daily_cols), flush=True)
        print("info columns:", ", ".join(info_cols), flush=True)
        jobs = [("stkdelists.parquet", "SELECT permno, delistingdt, delret, delactiontype, delstatustype, "
                 "delreasontype FROM crsp.stkdelists", None),
                ("stksecurityinfohist.parquet", f"SELECT {', '.join(info_cols)} FROM crsp.stksecurityinfohist", None)]
        daily_sql = (f"SELECT {', '.join(daily_cols)} FROM crsp.stkdlysecuritydata "
                     "WHERE dlycaldt BETWEEN %(s)s AND %(e)s")
        jobs += [(f"stkdly_{y}.parquet", daily_sql, {"s": f"{y}-01-01", "e": f"{y}-12-31"}) for y in a.years]
        for name, sql, params in jobs:
            path = a.out / name
            if path.exists():
                print(f"{name}: exists, skipped", flush=True)
                continue
            started = datetime.now()
            frame = tidy(query(conn, sql, params))
            tmp = path.with_suffix(".tmp")
            frame.to_parquet(tmp, index=False)
            tmp.replace(path)
            manifest["files"][name] = {"rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "query": sql, "params": params,
                                       "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            manifest_path.write_text(json.dumps(manifest, indent=1))
            print(f"{name}: {len(frame):,} rows ({(datetime.now() - started).seconds}s)", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
