"""Build idea B samples (images, labels, baseline scores) per year and decision time.

    .venv/Scripts/python -m scripts.intraday_b_build --first 2012 --last 2020 --workers 3

Writes C:/data/intraday_b/T<hhmm>/<year>_images.npy (packed 1-bit) and <year>_meta.parquet.
2021-2024 are sealed (vault.json) and refused by the bar loader.
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research import crsp
from orb_research.intraday_b import DECISIONS, build_samples, make_cube
from orb_research.taq_bars import load_bars

OUT = Path("C:/data/intraday_b")
LOOKBACK_DAYS = 40      # calendar days before the year: yesterday's bars, 14-day S2 history, 5-day lookbacks


def build_year(year: int, out: Path) -> str:
    t0 = time.time()
    start = pd.Timestamp(f"{year}-01-01") - pd.Timedelta(days=LOOKBACK_DAYS)
    start = max(start, pd.Timestamp("2012-01-01"))
    bars = load_bars(start, f"{year}-12-31", columns=["date", "permno", "b", "open", "high", "low", "close",
                                                       "volume", "vwap"])
    daily = crsp.load_daily(start, f"{year}-12-31")[["date", "permno", "prc", "openprc", "ret", "shrout"]]
    cube = make_cube(bars, daily)
    del bars
    in_year = cube.dates[cube.dates.year == year]
    msg = [f"{year}:"]
    for key, n_t in DECISIONS.items():
        folder = out / f"T{key}"
        folder.mkdir(parents=True, exist_ok=True)
        meta, packed = build_samples(cube, n_t, in_year)
        np.save(folder / f"{year}_images.npy", packed)
        meta.to_parquet(folder / f"{year}_meta.parquet", index=False)
        msg.append(f"T{key} {len(meta):,} samples on {meta['date'].nunique()} days")
    return " ".join(msg) + f" ({time.time() - t0:.0f}s)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--first", type=int, default=2012)
    p.add_argument("--last", type=int, default=2020)
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--out", type=Path, default=OUT)
    a = p.parse_args()
    with ProcessPoolExecutor(a.workers) as pool:
        for line in pool.map(build_year, range(a.first, a.last + 1), [a.out] * (a.last - a.first + 1)):
            print(line, flush=True)


if __name__ == "__main__":
    main()
