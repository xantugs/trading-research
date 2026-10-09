"""CRSP daily stock file from WRDS: download to yearly parquet files and load them through the
vault (docs/JKX_REPLICATION_PLAN.md).

Uses the legacy CRSP tables (crsp.dsf, crsp.dsenames, crsp.dsedelist). The password is never
handled here: libpq reads it from the user's pgpass file (scripts/wrds_pgpass.py).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import vault

HOST, PORT, DBNAME = "wrds-pgdata.wharton.upenn.edu", 9737, "wrds"
DEFAULT_DIR = Path("C:/data/crsp")
DATASET = "crsp_daily"

DAILY_SQL = """
SELECT a.permno, a.date, a.prc, a.openprc, a.askhi, a.bidlo, a.vol, a.ret, a.shrout, a.cfacpr,
       a.cfacshr, b.exchcd, b.shrcd
FROM crsp.dsf AS a
JOIN crsp.dsenames AS b
  ON a.permno = b.permno AND a.date BETWEEN b.namedt AND COALESCE(b.nameendt, DATE '9999-12-31')
WHERE a.date BETWEEN %(start)s AND %(end)s
"""
DELIST_SQL = "SELECT permno, dlstdt, dlret, dlstcd FROM crsp.dsedelist"
DTYPES = {"permno": "int32", "prc": "float64", "openprc": "float64", "askhi": "float64", "bidlo": "float64",
          "vol": "float64", "ret": "float64", "shrout": "float64", "cfacpr": "float64", "cfacshr": "float64",
          "exchcd": "Int16", "shrcd": "Int16"}


def connect(user: str):
    import psycopg2
    return psycopg2.connect(host=HOST, port=PORT, dbname=DBNAME, user=user, sslmode="require",
                            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)


def query_retry(user: str, sql: str, params: dict | None = None, tries: int = 5, log=print) -> pd.DataFrame:
    """One fresh connection per query, retried: WRDS drops long-lived connections now and then."""
    import time

    import psycopg2
    for attempt in range(1, tries + 1):
        try:
            conn = connect(user)
            try:
                return query(conn, sql, params)
            finally:
                conn.close()
        except psycopg2.OperationalError as exc:
            if attempt == tries:
                raise
            wait = 15 * attempt
            log(f"  connection lost ({str(exc).splitlines()[0]}); retry {attempt}/{tries - 1} in {wait}s")
            time.sleep(wait)


def query(conn, sql: str, params: dict | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(user: str, first_year: int, last_year: int, out: Path = DEFAULT_DIR, log=print) -> None:
    """Raw caches are immutable: an existing year is skipped, never overwritten."""
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    max_date = query_retry(user, "SELECT MAX(date) AS d FROM crsp.dsf", log=log)["d"].iloc[0]
    log(f"crsp.dsf ends {max_date}")
    jobs = [("dsedelist.parquet", DELIST_SQL, None)]
    jobs += [(f"dsf_{y}.parquet", DAILY_SQL, {"start": f"{y}-01-01", "end": f"{y}-12-31"})
             for y in range(first_year, last_year + 1)]
    for name, sql, params in jobs:
        path = out / name
        if path.exists():
            log(f"{name}: exists, skipped")
            continue
        frame = query_retry(user, sql, params, log=log)
        if name.startswith("dsf_"):
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.astype(DTYPES).sort_values(["date", "permno"]).reset_index(drop=True)
        else:
            frame["dlstdt"] = pd.to_datetime(frame["dlstdt"])
            frame["dlret"] = frame["dlret"].astype("float64")
        tmp = path.with_suffix(".tmp")
        frame.to_parquet(tmp, index=False)
        tmp.replace(path)
        manifest["files"][name] = {"rows": len(frame), "sha256": _sha256(path), "query": sql.strip(),
                                   "params": params, "source_max_date": str(max_date),
                                   "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        manifest_path.write_text(json.dumps(manifest, indent=1))
        log(f"{name}: {len(frame):,} rows")


def load_daily(start, end, experiment: str | None = None, crsp_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """CRSP daily rows with start <= date <= end. Refused by the vault if the range is sealed."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    vault.check(DATASET, start, end, experiment)
    frames = []
    for year in range(start.year, end.year + 1):
        path = Path(crsp_dir) / f"dsf_{year}.parquet"
        if not path.exists():
            raise FileNotFoundError(path)
        f = pd.read_parquet(path)
        frames.append(f[(f["date"] >= start) & (f["date"] <= end)])
    return pd.concat(frames, ignore_index=True)


def load_delist(end, experiment: str | None = None, crsp_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """Delistings up to `end` (later rows are dropped so they cannot leak sealed information).
    The legacy table ends with 2024; later delistings come from the new-format table (crsp.stkdelists),
    used only after the 2019 parity check has passed (orb_research.crsp_v2)."""
    f = pd.read_parquet(Path(crsp_dir) / "dsedelist.parquet")
    legacy_end = pd.Timestamp("2024-12-31")
    if pd.Timestamp(end) > legacy_end and (Path(crsp_dir) / "v2" / "PARITY_PASSED").exists():
        from .crsp_v2 import delist_to_legacy
        new = delist_to_legacy(pd.read_parquet(Path(crsp_dir) / "v2" / "stkdelists.parquet"))
        f = pd.concat([f, new[new["dlstdt"] > legacy_end]], ignore_index=True)
    return f[f["dlstdt"] <= pd.Timestamp(end)]
