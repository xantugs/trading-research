"""Build JKX chart images and labels from the CRSP parquet files, one file pair per year.

    .venv/Scripts/python -m scripts.jkx_build_images --days 5 --freq week --first-year 1993 --last-year 2019

Writes <out>/I<days>_<freq>/<year>_images.npy (packed, 1 bit per pixel) and <year>_meta.parquet.
Data after --data-end (default 2019-12-31, the paper's sample end) are never loaded unless an
experiment has unsealed them (orb_research/vault.py).
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from orb_research import crsp
from orb_research.jkx_images import build_samples, formation_dates, panel_from_crsp

LOOKBACK_DAYS = 400       # calendar days before the year: 252 trading days of momentum, 119 for 60-day images
FORWARD_DAYS = 130        # calendar days after the year: 60 trading days of labels and the next formation


def build_year(year: int, days: int, freq: str, out: Path, data_end: str, experiment: str | None,
               crsp_dir: Path, share_codes: tuple[int, ...] | None) -> str:
    start = time.time()
    first = pd.Timestamp(f"{year}-01-01") - pd.Timedelta(days=LOOKBACK_DAYS)
    last = min(pd.Timestamp(f"{year}-12-31") + pd.Timedelta(days=FORWARD_DAYS), pd.Timestamp(data_end))
    if pd.Timestamp(f"{year}-01-01") > last:
        return f"{year}: after data end, skipped"
    daily = crsp.load_daily(max(first, pd.Timestamp("1992-01-01")), last, experiment, crsp_dir)
    delist = crsp.load_delist(last, experiment, crsp_dir)
    panel = panel_from_crsp(daily, delist, share_codes)
    form = formation_dates(panel.dates, freq)
    form = form[form < panel.dates[-1]]          # the final period may be cut off by the data end
    in_year = form[form.year == year]
    samples = build_samples(panel, days, form, build=in_year, data_end=last)
    np.save(out / f"{year}_images.npy", samples.packed)
    samples.meta.to_parquet(out / f"{year}_meta.parquet", index=False)
    return f"{year}: {len(samples.meta):,} images on {samples.meta['date'].nunique() if len(samples.meta) else 0} dates ({time.time() - start:.0f}s)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, choices=(5, 20, 60), required=True)
    p.add_argument("--freq", choices=("week", "month", "quarter"), required=True)
    p.add_argument("--first-year", type=int, default=1993)
    p.add_argument("--last-year", type=int, default=2019)
    p.add_argument("--data-end", default="2019-12-31")
    p.add_argument("--experiment", default=None, help="needed to read a range unsealed by this experiment")
    p.add_argument("--share-codes", default=None, help="e.g. 10,11 (default: all, as in the paper)")
    p.add_argument("--crsp-dir", type=Path, default=crsp.DEFAULT_DIR)
    p.add_argument("--out", type=Path, default=Path("C:/data/jkx"))
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    codes = tuple(int(c) for c in a.share_codes.split(",")) if a.share_codes else None
    tag = f"I{a.days}_{a.freq}" + (f"_shr{'-'.join(map(str, codes))}" if codes else "")
    out = a.out / tag
    out.mkdir(parents=True, exist_ok=True)
    years = range(a.first_year, a.last_year + 1)
    with ProcessPoolExecutor(a.workers) as pool:
        jobs = [pool.submit(build_year, y, a.days, a.freq, out, a.data_end, a.experiment, a.crsp_dir, codes)
                for y in years]
        for job in jobs:
            print(job.result(), flush=True)


if __name__ == "__main__":
    main()
