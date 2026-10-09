"""Download CRSP new-format daily data for one year in batches of PERMNOs (indexed), over ONE
connection. The date-only query on crsp.stkdlysecuritydata streams for hours; batching by
PERMNO uses the table's index. Writes C:/data/crsp/v2/stkdly_<year>.parquet like
scripts.download_crsp_v2.

    .venv/Scripts/python -m scripts.download_crsp_v2_chunks --user <wrds user> --years 2019 2025
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

import pandas as pd

from orb_research.crsp import DEFAULT_DIR, connect, query
from scripts.download_crsp_v2 import DAILY_WANTED, columns, tidy


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    p.add_argument("--years", type=int, nargs="+", required=True)
    p.add_argument("--batch", type=int, default=400)
    a = p.parse_args()
    out = DEFAULT_DIR / "v2"
    info = pd.read_parquet(out / "stksecurityinfohist.parquet")
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    conn = connect(a.user)
    print("connected", flush=True)
    try:
        cols = [c for c in DAILY_WANTED if c in columns(conn, "crsp.stkdlysecuritydata")]
        sql = (f"SELECT {', '.join(cols)} FROM crsp.stkdlysecuritydata "
               "WHERE permno = ANY(%(p)s) AND dlycaldt BETWEEN %(s)s AND %(e)s")
        for year in a.years:
            path = out / f"stkdly_{year}.parquet"
            if path.exists():
                print(f"{path.name}: exists, skipped", flush=True)
                continue
            s, e = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year}-12-31")
            alive = info[(info["secinfostartdt"] <= e) & (info["secinfoenddt"].isna() | (info["secinfoenddt"] >= s))]
            permnos = sorted(int(x) for x in alive["permno"].unique())
            print(f"{year}: {len(permnos):,} permnos in {len(permnos) // a.batch + 1} batches", flush=True)
            parts, t0 = [], time.time()
            for i in range(0, len(permnos), a.batch):
                t = time.time()
                parts.append(query(conn, sql, {"p": permnos[i:i + a.batch], "s": s.date(), "e": e.date()}))
                print(f"  batch {i // a.batch + 1}: {len(parts[-1]):,} rows in {time.time() - t:.0f}s", flush=True)
            frame = tidy(pd.concat(parts, ignore_index=True))
            tmp = path.with_suffix(".tmp")
            frame.to_parquet(tmp, index=False)
            tmp.replace(path)
            manifest["files"][path.name] = {"rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                            "query": sql, "params": {"year": year, "batch": a.batch},
                                            "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            manifest_path.write_text(json.dumps(manifest, indent=1))
            print(f"{path.name}: {len(frame):,} rows ({time.time() - t0:.0f}s)", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
