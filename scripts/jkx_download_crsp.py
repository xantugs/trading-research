"""Download the CRSP daily stock file (1992 on) to yearly parquet files.

    .venv/Scripts/python -m scripts.jkx_download_crsp --user <wrds username>
"""
from __future__ import annotations

import argparse
from pathlib import Path

from orb_research.crsp import DEFAULT_DIR, download


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    p.add_argument("--first-year", type=int, default=1992)
    p.add_argument("--last-year", type=int, default=2024)
    p.add_argument("--out", type=Path, default=DEFAULT_DIR)
    a = p.parse_args()
    download(a.user, a.first_year, a.last_year, a.out)


if __name__ == "__main__":
    main()
