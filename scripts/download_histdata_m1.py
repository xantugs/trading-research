#!/usr/bin/env python3
"""Download HistData.com free M1 ASCII bars and cache them as UTC monthly Parquet.

HistData says its bars are stamped in EST without daylight saving, but checked against
FTMO's US100 bars (2021-10 to 2024) the NSXUSD stamps follow New York wall-clock time,
daylight saving included: winter aligns at UTC-5, summer at UTC-4.  So every bar is read
as America/New_York local time and converted to UTC.
Bars are bid quotes with no spread and no volume, so the cache stores both as 0 and a
study must model the spread itself.  The yearly zips are kept next to the cache.

    python -m scripts.download_histdata_m1 --symbol NSXUSD --from-year 2010 --to-year 2024
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import zipfile
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orb_research.data_loader import validate_m1_bars  # noqa: E402

SITE = "https://www.histdata.com"
SERVER = "HistData"
AGENT = {"User-Agent": "Mozilla/5.0 (trading-research; one request per year)"}


def fetch_year(opener, symbol: str, year: int) -> bytes:
    page = f"{SITE}/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{symbol.lower()}/{year}"
    html = opener.open(Request(page, headers=AGENT), timeout=60).read().decode("utf-8", "replace")
    token = re.search(r'name="tk"[^>]*value="([^"]+)"', html) or re.search(r'id="tk"[^>]*value="([^"]+)"', html)
    if not token:
        raise RuntimeError(f"{year}: no download token on {page}")
    form = urlencode({"tk": token.group(1), "date": str(year), "datemonth": str(year), "platform": "ASCII",
                      "timeframe": "M1", "fxpair": symbol.upper()}).encode()
    request = Request(f"{SITE}/get.php", data=form, headers={**AGENT, "Referer": page,
                                                             "Content-Type": "application/x-www-form-urlencoded"})
    body = opener.open(request, timeout=300).read()
    if not body.startswith(b"PK"):
        raise RuntimeError(f"{year}: the response is not a zip ({body[:80]!r})")
    return body


def parse_zip(raw: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        text = archive.read(name).decode("utf-8")
    frame = pd.read_csv(io.StringIO(text), sep=";", header=None,
                        names=["stamp", "open", "high", "low", "close", "volume"])
    local = pd.DatetimeIndex(pd.to_datetime(frame.stamp, format="%Y%m%d %H%M%S"))
    # New York wall time; the hour that does not exist or repeats at a clock change falls on a
    # Sunday night before the reopen, so it carries no bars worth guessing about.
    index = local.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT").tz_convert("UTC")
    frame = frame[~index.isna()]
    index = index[~index.isna()]
    out = pd.DataFrame({"open": frame.open.to_numpy(float), "high": frame.high.to_numpy(float),
                        "low": frame.low.to_numpy(float), "close": frame.close.to_numpy(float),
                        "tick_volume": 0, "spread": 0, "real_volume": 0}, index=index)
    out = out.loc[~out.index.duplicated(keep="first")].sort_index()
    return validate_m1_bars(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="NSXUSD")
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument("--cache-dir", default="C:/data/histdata_m1")
    args = parser.parse_args(argv)
    symbol = args.symbol.upper()
    cache = Path(args.cache_dir) / SERVER / symbol
    raw_dir = Path(args.cache_dir) / "zips"
    cache.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    summary = {}
    for year in range(args.from_year, args.to_year + 1):
        zip_path = raw_dir / f"HISTDATA_COM_ASCII_{symbol}_M1_{year}.zip"
        if not zip_path.exists():
            zip_path.write_bytes(fetch_year(opener, symbol, year))
            time.sleep(2)
        bars = parse_zip(zip_path.read_bytes())
        for month in pd.period_range(f"{year}-01", f"{year}-12", freq="M"):
            start = month.start_time.tz_localize("UTC")
            part = bars.loc[(bars.index >= start) & (bars.index < start + pd.offsets.MonthBegin(1))]
            part.to_parquet(cache / month.strftime("%Y%m.parquet"))  # empty months are kept as gaps
        summary[year] = {"zip_bytes": zip_path.stat().st_size, "bars": int(len(bars)),
                         "first_utc": bars.index[0].isoformat(), "last_utc": bars.index[-1].isoformat(),
                         "weekdays_with_bars": int(pd.Index(bars.index.normalize().unique()).weekday.isin(range(5)).sum())}
        print(f"{symbol} {year}: {len(bars):,} bars, {zip_path.stat().st_size / 1e6:.1f} MB", flush=True)
    decimals = max(len(f"{v:.6f}".rstrip("0").split(".")[1]) for v in bars.close.iloc[:2000])
    (cache / "source.json").write_text(json.dumps({
        "server": SERVER, "symbol": symbol, "chart_mode": "bid", "point": 10 ** -max(decimals, 1),
        "contract_size": 1.0, "provider": "histdata.com free ASCII M1",
        "clock": "New York wall time with daylight saving (checked against FTMO), converted to UTC",
        "spread": "not recorded; stored as 0", "years": summary}, indent=2) + "\n", encoding="utf-8")
    print(f"cache: {cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
