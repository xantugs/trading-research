"""Cache FTMO daily (D1) bars from the running MT5 terminal, one parquet file per symbol.

    .venv/Scripts/python -m scripts.download_mt5_d1 --end 2026-01-01

Bars dated on or after --end are dropped before anything is written, so a sealed period
(2026 for pilot A, docs/JKX_TRANSFER_PILOT.md) never reaches disk. Requests are by bar count
(long date-range requests time out in the terminal); symbols already cached are skipped. Bar times are the broker's server day (New York + 7 h).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone  # noqa: F401
from pathlib import Path

import pandas as pd

GROUPS = ("Forex", "Exotics", "Metals CFD", "Cash CFD", "Cash II CFD", "Cash III CFD", "Commodities",
          "Agriculture", "Crypto I CFD", "Crypto II CFD", "Equities I CFD", "Equities II CFD")


def main() -> None:
    import MetaTrader5 as mt5

    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2000-01-01")
    p.add_argument("--end", default="2026-01-01", help="first day NOT downloaded")
    p.add_argument("--out", type=Path, default=Path("C:/data/ftmo_d1"))
    p.add_argument("--bars", type=int, default=7000, help="D1 bars requested per symbol (newest first)")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if not mt5.initialize(timeout=300_000):
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    manifest_path = a.out / "manifest.json"

    def save() -> None:   # atomic: an interrupted run must not leave an empty manifest
        tmp = manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=1))
        tmp.replace(manifest_path)
    manifest = {"server": mt5.account_info().server, "start": a.start, "end_exclusive": a.end,
                "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": {}}
    try:
        symbols = sorted(mt5.symbols_get(), key=lambda x: x.path.startswith("Equities"))
        for s in symbols:
            group = s.path.split("\\")[0]
            if group not in GROUPS:
                continue
            cached = a.out / f"{s.name}.parquet"
            if cached.exists():                    # resume: rebuild the entry from the file itself
                f = pd.read_parquet(cached)
                manifest["symbols"][s.name] = {"group": group, "bars": len(f), "first": str(f["date"].iloc[0].date()),
                                               "last": str(f["date"].iloc[-1].date())}
                continue
            mt5.symbol_select(s.name, True)
            rates = mt5.copy_rates_from_pos(s.name, mt5.TIMEFRAME_D1, 0, a.bars)
            if rates is None or len(rates) == 0:
                print(f"{s.name}: no bars ({mt5.last_error()})", flush=True)
                continue
            f = pd.DataFrame(rates)
            f["date"] = pd.to_datetime(f["time"], unit="s")
            f = f[(f["date"] >= pd.Timestamp(a.start)) & (f["date"] < pd.Timestamp(a.end))]
            if f.empty:
                continue
            f = f[["date", "open", "high", "low", "close", "tick_volume", "spread"]]
            tmp = a.out / f"{s.name}.parquet.tmp"
            f.to_parquet(tmp, index=False)
            tmp.replace(a.out / f"{s.name}.parquet")      # atomic: no half-written files
            manifest["symbols"][s.name] = {"group": group, "bars": len(f), "first": str(f["date"].iloc[0].date()),
                                           "last": str(f["date"].iloc[-1].date())}
            print(f"{s.name:12s} {group:16s} {len(f):5d} bars {f['date'].iloc[0].date()} .. {f['date'].iloc[-1].date()}",
                  flush=True)
            save()
    finally:
        mt5.shutdown()
        save()


if __name__ == "__main__":
    main()
