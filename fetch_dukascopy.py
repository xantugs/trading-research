"""Download Dukascopy M1 bid candles with cached, validated BI5 artifacts.

Date range is [--from, --to) in UTC. Default clock is New York wall time +7.
The spread is an explicit constant assumption, NOT inferred from asynchronous
bid/ask candle closes. Native feed volume is a liquidity proxy, not tick count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import math
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from orb.data import normalise, resample
from orb.timezones import to_server_time

BASE = "https://datafeed.dukascopy.com/datafeed"
CANDLE = struct.Struct(">5If")
TICK = struct.Struct(">3I2f")
SCALES = {"USATECHIDXUSD": 1000, "USA500IDXUSD": 1000, "USA30IDXUSD": 1000,
          "DEUIDXEUR": 1000, "EURUSD": 100000, "GBPUSD": 100000, "USDJPY": 1000}
FORMAT_VERSION = 1


def day_url(symbol: str, day: datetime, name: str) -> str:
    return f"{BASE}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{name}"


def unpack(raw: bytes | None, record: struct.Struct) -> np.ndarray:
    if not raw:
        return np.empty((0, len(record.unpack(bytes(record.size)))))
    data = lzma.decompress(raw)
    if len(data) % record.size:
        raise ValueError(f"Truncated BI5 payload: {len(data)} bytes / {record.size}")
    rows = np.asarray(list(record.iter_unpack(data)), dtype=float)
    if rows.size and not np.isfinite(rows).all():
        raise ValueError("Non-finite BI5 fields")
    return rows


def decode_candles(raw: bytes | None, day: datetime, scale: float) -> pd.DataFrame:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be positive")
    arr = unpack(raw, CANDLE)
    if not arr.size:
        return pd.DataFrame()
    if ((arr[:, 0] >= 86400) | (arr[:, 0] % 60 != 0)).any():
        raise ValueError("Invalid M1 candle timestamp")
    frame = pd.DataFrame(
        {"open": arr[:, 1] / scale, "high": arr[:, 4] / scale,
         "low": arr[:, 3] / scale, "close": arr[:, 2] / scale,
         "tick_volume": arr[:, 5], "spread": 0.0},
        index=pd.Timestamp(day) + pd.to_timedelta(arr[:, 0], unit="s"),
    )
    # Zero-volume candles may still contain useful OHLC. Never discard selectively.
    return normalise(frame)


def decode_ticks(raw: bytes | None, hour_start: datetime, scale: float) -> pd.DataFrame:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be positive")
    arr = unpack(raw, TICK)
    if not arr.size:
        return pd.DataFrame()
    if (arr[:, 0] >= 3600000).any():
        raise ValueError("Invalid tick timestamp")
    if (arr[:, 1] < arr[:, 2]).any() or (arr[:, 1:3] <= 0).any() or (arr[:, 3:] < 0).any():
        raise ValueError("Invalid tick quote")
    return pd.DataFrame(
        {"ask": arr[:, 1] / scale, "bid": arr[:, 2] / scale,
         "ask_volume": arr[:, 3], "bid_volume": arr[:, 4]},
        index=pd.Timestamp(hour_start) + pd.to_timedelta(arr[:, 0], unit="ms"),
    )


def fetch(url: str, retries: int = 4, cache: Path | None = None) -> bytes | None:
    if retries < 1:
        raise ValueError("retries must be >= 1")
    artifact = cache / (hashlib.sha256(url.encode()).hexdigest() + ".bi5") if cache else None
    if artifact and artifact.exists():
        raw = artifact.read_bytes()
        lzma.decompress(raw)  # corrupted cache is an error, never silently trusted
        return raw
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "orb-backtest/1.3"}), timeout=30) as response:
                raw = response.read()
            if not raw:
                return None
            lzma.decompress(raw)
            if artifact:
                artifact.parent.mkdir(parents=True, exist_ok=True)
                temp = artifact.with_suffix(".part")
                temp.write_bytes(raw)
                temp.replace(artifact)
            return raw
        except HTTPError as exc:
            if exc.code == 404:
                return None  # never cache absence: it may be published later
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except (URLError, TimeoutError, OSError, lzma.LZMAError):
            if attempt == retries - 1:
                raise ConnectionError(
                    f"Cannot reach Dukascopy at {url}. Cached files are preserved; rerun to resume. "
                    "If datafeed.dukascopy.com:443 is blocked, try another connection or export M1 CSV "
                    "from Dukascopy and use run_all.py --source csv --bars FILE."
                ) from None
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError("Unreachable retry state")


def write_mt5_format(bars: pd.DataFrame, path: Path) -> None:
    mt5 = pd.DataFrame({
        "<DATE>": bars.index.strftime("%Y.%m.%d"), "<TIME>": bars.index.strftime("%H:%M:%S"),
        "<OPEN>": bars["open"].values, "<HIGH>": bars["high"].values,
        "<LOW>": bars["low"].values, "<CLOSE>": bars["close"].values,
        # Native candle volume is not tick count; provide one synthetic count per bar.
        "<TICKVOL>": 1, "<VOL>": 0, "<SPREAD>": bars["spread"].round().astype(int).values,
    })
    mt5.to_csv(path, sep="\t", index=False, float_format="%.8f")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reusable_dataset(out: Path, expected: dict, ticks: bool) -> dict | None:
    """Return metadata only when every declared local artifact still matches."""
    metadata_path = out / f"{expected['symbol']}_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if any(metadata.get(key) != value for key, value in expected.items()):
        return None
    artifacts = {
        "m1_sha256": out / f"{expected['symbol']}_M1.csv",
        "m15_sha256": out / f"{expected['symbol']}_M15.csv",
        "mt5_sha256": out / f"{expected['symbol']}_M1_mt5.csv",
    }
    if ticks:
        artifacts["ticks_sha256"] = out / f"{expected['symbol']}_ticks.csv"
    if any(not path.is_file() or metadata.get(key) != file_hash(path) for key, path in artifacts.items()):
        return None
    return {**metadata, "reused_local_dataset": True}


def download(symbol: str, start: str, end: str, out: Path, *, tz: str = "ny+7",
             scale: float | None = None, point: float = 0.01, spread: float = 1.0,
             workers: int = 4, cache: Path | None = None, ticks: bool = False,
             reuse: bool = True) -> dict:
    symbol = symbol.upper()
    if not re.fullmatch(r"[A-Z0-9]{3,32}", symbol):
        raise ValueError("symbol must contain only 3-32 letters/digits")
    scale = scale if scale is not None else SCALES.get(symbol)
    if scale is None:
        raise ValueError("Unknown instrument price scale; supply --scale explicitly")
    if not all(math.isfinite(x) and x > 0 for x in (scale, point)):
        raise ValueError("scale and point must be finite and positive")
    if not math.isfinite(spread) or spread < 0 or not 1 <= workers <= 8:
        raise ValueError("spread must be nonnegative; workers must be 1-8")
    a, b = datetime.strptime(start, "%Y-%m-%d"), datetime.strptime(end, "%Y-%m-%d")
    if b <= a:
        raise ValueError("--to must be later than --from (end exclusive)")
    if b.date() > datetime.now(timezone.utc).date():
        raise ValueError("--to must be no later than today UTC; only completed UTC days")
    to_server_time(pd.DatetimeIndex([a]), tz)  # validate before network
    days = [a + timedelta(days=i) for i in range((b - a).days)]
    expected = {
        "format_version": FORMAT_VERSION, "provider": "dukascopy", "symbol": symbol,
        "from_utc": start, "to_utc_exclusive": end, "clock": tz,
        "price_scale": scale, "point": point, "spread_price_units": spread,
        "ticks_requested": ticks,
    }
    if reuse and (metadata := reusable_dataset(out, expected, ticks)) is not None:
        print(f"Reusing verified Dukascopy dataset: {out.resolve()}", flush=True)
        return metadata
    out.mkdir(parents=True, exist_ok=True)
    frames, absent = [], []
    print(f"{symbol}: {len(days)} UTC days [{start}, {end}), clock {tz}", flush=True)

    def one(day):
        raw = fetch(day_url(symbol, day, "BID_candles_min_1.bi5"), cache=cache)
        return day, decode_candles(raw, day, scale)

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Bound submitted work so a blocked provider stops after at most one worker batch.
        for offset in range(0, len(days), workers):
            futures = [pool.submit(one, day) for day in days[offset:offset + workers]]
            for future in futures:
                day, frame = future.result()
                completed += 1
                if frame.empty:
                    absent.append(day.strftime("%Y-%m-%d"))
                else:
                    frames.append(frame)
                if completed % 20 == 0 or completed == len(days):
                    print(f"  {completed}/{len(days)} days; {len(absent)} absent/empty", flush=True)
    if not frames:
        raise ValueError("No data downloaded; check symbol and date range")
    bars = pd.concat(frames).sort_index()
    bars["spread"] = spread / point
    bars.index = to_server_time(bars.index, tz)
    bars = normalise(bars)  # fail on ambiguous duplicate wall times
    m1_path = out / f"{symbol}_M1.csv"
    bars.to_csv(m1_path, float_format="%.8f")
    resample(bars, 15).to_csv(out / f"{symbol}_M15.csv", float_format="%.8f")
    write_mt5_format(bars, out / f"{symbol}_M1_mt5.csv")

    tick_path = out / f"{symbol}_ticks.csv"
    tick_rows = 0
    if ticks:
        # Stream one day at a time to bound memory; raw tick volume is not M1 volume.
        temp = tick_path.with_suffix(".part")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            for day in days:
                for hour in range(24):
                    raw = fetch(day_url(symbol, day, f"{hour:02d}h_ticks.bi5"), cache=cache)
                    frame = decode_ticks(raw, day + timedelta(hours=hour), scale)
                    if not frame.empty:
                        frame.index = to_server_time(frame.index, tz)
                        frame.index.name = "time"
                        frame.to_csv(handle, header=tick_rows == 0, float_format="%.8f")
                        tick_rows += len(frame)
                print(f"  ticks {day:%Y-%m-%d}: {tick_rows:,} cumulative", flush=True)
        if not tick_rows:
            temp.unlink()
            raise ValueError("Ticks requested but no ticks returned")
        temp.replace(tick_path)
    metadata = {
        **expected, "rows_m1": len(bars),
        "first_server_time": str(bars.index[0]), "last_server_time": str(bars.index[-1]),
        "absent_utc_days": absent, "spread_model": "constant assumption",
        "spread_price_units": spread, "volume_semantics": "native candle liquidity proxy, not tick count",
        "mt5_import_volume": "synthetic one count per M1; disable volume filters when reconciling",
        "ticks_rows": tick_rows, "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "m1_sha256": file_hash(m1_path),
        "m15_sha256": file_hash(out / f"{symbol}_M15.csv"),
        "mt5_sha256": file_hash(out / f"{symbol}_M1_mt5.csv"),
    }
    if ticks:
        metadata["ticks_sha256"] = file_hash(tick_path)
    metadata_path = out / f"{symbol}_metadata.json"
    temp_metadata = metadata_path.with_suffix(".json.part")
    temp_metadata.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temp_metadata.replace(metadata_path)
    print(f"Saved {len(bars):,} M1 bars to {m1_path}; first close {bars.close.iloc[0]:.3f}", flush=True)
    return metadata


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="USATECHIDXUSD")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--tz", default="ny+7")
    ap.add_argument("--scale", type=float)
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument("--spread", type=float, default=1.0, help="Assumed spread in price units")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--cache", type=Path, default=Path(".cache/dukascopy"))
    ap.add_argument("--ticks", action="store_true")
    ap.add_argument("--refresh-data", action="store_true")
    args = ap.parse_args()
    try:
        refresh_data = args.refresh_data
        delattr(args, "refresh_data")
        download(**vars(args), reuse=not refresh_data)
    except (ValueError, OSError, lzma.LZMAError) as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
