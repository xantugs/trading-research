"""WRDS TAQ tick loading and bounded, day-at-a-time NBBO access.

``load_ticks`` remains the small-file loader used by the chart overlay.  The
``TickStore`` is the execution-oriented interface: it reads one WRDS daily quote
file at a time, projects only the timestamp and NBBO columns, preserves TAQ's
sub-microsecond ordering, and keeps a bounded LRU cache.  Source timestamps are
New York wall time.  Callers must state the fixed offset into their naive
strategy clock explicitly.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from threading import RLock
from typing import Final

import numpy as np
import pandas as pd


_SEQUENCE_COLUMNS: Final = ("qu_seqnum", "tr_seqnum", "seqnum", "sequence_number")
__all__ = ["QuoteDay", "TickCoverage", "TickStore", "load_ticks", "tick_quality"]


def _naive_timestamp(value: str | datetime | pd.Timestamp, label: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if pd.isna(stamp):
        raise ValueError(f"Invalid {label}: {value!r}")
    if stamp.tz is not None:
        raise ValueError(f"{label} must use the naive strategy clock")
    return stamp


def _normalise_columns(raw: pd.DataFrame) -> pd.DataFrame:
    raw.columns = [str(column).strip().lower() for column in raw.columns]
    raw.rename(columns={
        "best_bid": "bid", "best_ask": "ask", "trade_price": "price",
        "price_m": "price", "size_m": "size",
    }, inplace=True)
    return raw


def _wrds_timestamps(raw: pd.DataFrame) -> pd.Series:
    """Combine WRDS ``date``, microsecond ``time_m`` and nanosecond remainder."""
    dates = pd.to_datetime(raw["date"], errors="coerce").dt.normalize()
    times = raw["time_m"]
    if isinstance(times.dtype, pd.DatetimeTZDtype):
        # This layout is not expected from WRDS, but converting to strings would
        # silently attach a date and produce the wrong duration.
        raise ValueError("WRDS time_m must be a time-of-day duration, not timezone-aware")
    if pd.api.types.is_timedelta64_dtype(times.dtype):
        elapsed = pd.to_timedelta(times, errors="coerce")
    else:
        elapsed = pd.to_timedelta(times.astype("string").str.strip(), errors="coerce")
    stamps = dates + elapsed
    if "time_m_nano" in raw:
        nanos = pd.to_numeric(raw["time_m_nano"], errors="coerce")
        valid_nanos = nanos.isna() | (nanos.between(0, 999) & nanos.mod(1).eq(0))
        stamps = stamps.where(valid_nanos)
        stamps = stamps + pd.to_timedelta(nanos.fillna(0), unit="ns")
    return stamps


def _day_is_expected(raw: pd.DataFrame, source_date: date) -> np.ndarray:
    """Row mask for `date` matching the file's declared source date."""
    return (raw["date"].astype("string").str.strip()
            .eq(source_date.isoformat()).fillna(False).to_numpy())


def _timedelta_day_timestamps(raw: pd.DataFrame, column: pd.Series,
                              source_date: date) -> pd.Series:
    """Vectorised timestamps for an already-typed timedelta `time_m` column.

    Produces exactly what the ASCII parser produces for the same values, without
    materialising a string per row.
    """
    count = len(raw)
    elapsed = column.to_numpy(dtype="timedelta64[ns]")
    valid = ~np.isnat(elapsed)
    elapsed_ns = np.where(valid, elapsed.astype("int64"), 0)
    valid &= (elapsed_ns >= 0) & (elapsed_ns < 86_400 * 1_000_000_000)

    if "time_m_nano" in raw:
        nano_values = pd.to_numeric(raw["time_m_nano"], errors="coerce").to_numpy(
            dtype=float, na_value=np.nan)
        nano_missing = ~np.isfinite(nano_values)
        valid &= nano_missing | ((nano_values >= 0) & (nano_values <= 999)
                                 & (nano_values == np.floor(nano_values)))
        nanos = np.where(nano_missing, 0, nano_values).astype(np.int64)
    else:
        nanos = np.zeros(count, dtype=np.int64)

    valid &= _day_is_expected(raw, source_date)
    values = np.full(count, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    values[valid] = (np.datetime64(source_date.isoformat(), "ns")
                     + (elapsed_ns[valid] + nanos[valid]).astype("timedelta64[ns]"))
    return pd.Series(values, index=raw.index)


def _wrds_day_timestamps(raw: pd.DataFrame, source_date: date) -> pd.Series:
    """Fast validated timestamp parser for one WRDS daily Parquet file.

    WRDS normally stores ``time_m`` as ``HH:MM:SS.ffffff`` and occasionally as
    ``HH:MM:SS`` on an exact second.  Parsing those fixed ASCII layouts directly
    avoids allocating a million Python strings/timedeltas for every trading day.
    Rows outside either shape remain NaT and are counted by the caller.
    """
    count = len(raw)
    column = raw["time_m"]
    if pd.api.types.is_timedelta64_dtype(column):
        # WRDS Parquet usually lands here: a Postgres `time` becomes timedelta64.
        # Rendering it to ASCII first invokes a per-row Python formatter, roughly
        # twenty times the cost of the parse it feeds, so read the nanoseconds
        # straight out of the array instead.
        return _timedelta_day_timestamps(raw, column, source_date)
    encoded = column.fillna("").astype(str).to_numpy(dtype="S15")
    chars = encoded.view(np.uint8).reshape(count, 15)
    digit_positions = (0, 1, 3, 4, 6, 7)
    base_digits = np.ones(count, dtype=bool)
    for position in digit_positions:
        base_digits &= (chars[:, position] >= ord("0")) & (chars[:, position] <= ord("9"))
    separators = (chars[:, 2] == ord(":")) & (chars[:, 5] == ord(":"))
    whole_second = (chars[:, 8] == 0) & np.all(chars[:, 9:] == 0, axis=1)
    fractional = chars[:, 8] == ord(".")
    for position in range(9, 15):
        fractional &= (chars[:, position] >= ord("0")) & (chars[:, position] <= ord("9"))

    hour = (chars[:, 0].astype(np.int64) - 48) * 10 + chars[:, 1].astype(np.int64) - 48
    minute = (chars[:, 3].astype(np.int64) - 48) * 10 + chars[:, 4].astype(np.int64) - 48
    second = (chars[:, 6].astype(np.int64) - 48) * 10 + chars[:, 7].astype(np.int64) - 48
    clock_valid = base_digits & separators & (whole_second | fractional)
    clock_valid &= (hour >= 0) & (hour < 24) & (minute >= 0) & (minute < 60)
    clock_valid &= (second >= 0) & (second < 60)

    microseconds = np.zeros(count, dtype=np.int64)
    for position in range(9, 15):
        microseconds = microseconds * 10 + np.where(fractional, chars[:, position] - 48, 0)
    nanos = (pd.to_numeric(raw["time_m_nano"], errors="coerce")
             if "time_m_nano" in raw else pd.Series(0, index=raw.index, dtype=float))
    nano_values = nanos.to_numpy(dtype=float, na_value=np.nan)
    nano_missing = ~np.isfinite(nano_values)
    nano_valid = nano_missing | ((nano_values >= 0) & (nano_values <= 999)
                                 & (nano_values == np.floor(nano_values)))
    nano_values = np.where(nano_missing, 0, nano_values).astype(np.int64)

    date_valid = _day_is_expected(raw, source_date)
    valid = clock_valid & nano_valid & date_valid
    elapsed = ((hour * 3600 + minute * 60 + second) * 1_000_000_000
               + microseconds * 1_000 + nano_values)
    values = np.full(count, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    values[valid] = (np.datetime64(source_date.isoformat(), "ns")
                     + elapsed[valid].astype("timedelta64[ns]"))
    return pd.Series(values, index=raw.index)


def _timestamp_series(raw: pd.DataFrame) -> pd.Series:
    if "date" in raw and "time_m" in raw:
        return _wrds_timestamps(raw)
    if "date" in raw and "time" in raw:
        return pd.to_datetime(
            raw["date"].astype("string") + " " + raw["time"].astype("string"), errors="coerce",
        )
    field = next((value for value in ("timestamp", "datetime", "time") if value in raw), None)
    if field is None:
        raise ValueError("Tick file needs time, timestamp, or date + time_m columns")
    return pd.to_datetime(raw[field], errors="coerce")


def _sort_ticks(raw: pd.DataFrame, stamps: pd.Series, offset_hours: int) -> tuple[pd.DataFrame, int]:
    if isinstance(stamps.dtype, pd.DatetimeTZDtype):
        stamps = stamps.dt.tz_localize(None)
    if offset_hours:
        stamps = stamps + pd.Timedelta(hours=offset_hours)
    output = raw.copy()
    output["_stamp"] = stamps
    output["_source_row"] = np.arange(len(output), dtype=np.int64)
    dropped_timestamps = int(output["_stamp"].isna().sum())
    output = output[output["_stamp"].notna()]
    sequence = next((column for column in _SEQUENCE_COLUMNS if column in output), None)
    if sequence:
        output["_sequence_sort"] = pd.to_numeric(output[sequence], errors="coerce")
    ordering = ["_stamp"] + (["_sequence_sort"] if sequence else []) + ["_source_row"]
    output = output.sort_values(ordering, kind="stable", na_position="last")
    output = output.drop(columns=["_source_row", "_sequence_sort"], errors="ignore").set_index("_stamp")
    output.index.name = "time"
    return output, dropped_timestamps


def _read(source: str | Path | bytes, name: str | None) -> pd.DataFrame:
    hint = (name or str(source) if not isinstance(source, bytes) else name or "").lower()
    payload = BytesIO(source) if isinstance(source, bytes) else source
    if hint.endswith((".parquet", ".pq")):
        return pd.read_parquet(payload)
    return pd.read_csv(payload, compression="gzip" if hint.endswith(".gz") else "infer")


def load_ticks(source: str | Path | bytes, name: str | None = None,
               strategy_offset_hours: int = 0) -> pd.DataFrame:
    """Return standardized ticks, optionally shifted from source to strategy clock."""
    raw = _normalise_columns(_read(source, name))
    if raw.empty:
        raise ValueError("Tick file is empty")
    stamps = _timestamp_series(raw)

    prices = [column for column in ("bid", "ask", "price") if column in raw]
    if not prices:
        raise ValueError("Tick file needs bid/ask quotes or a price column")
    output = raw.drop(columns=[column for column in ("timestamp", "datetime", "time", "date", "time_m")
                               if column in raw], errors="ignore").copy()
    output, dropped_timestamps = _sort_ticks(output, stamps, strategy_offset_hours)
    for column in prices:
        output[column] = pd.to_numeric(output[column], errors="coerce")
    keep = prices + [column for column in output if column not in prices]
    output = output[keep]
    output.attrs["dropped_timestamps"] = dropped_timestamps
    return output


@dataclass(frozen=True, slots=True)
class TickCoverage:
    """Observed bounds and filtering counts for one WRDS quote file."""

    source_date: date
    source_path: Path
    source_rows: int
    rows: int
    dropped_timestamps: int
    dropped_invalid: int
    dropped_crossed: int
    duplicate_timestamps: int
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    strategy_offset_hours: int

    @property
    def dropped_rows(self) -> int:
        """Total rows excluded before exposure to an execution model."""
        return self.source_rows - self.rows

    def bounds_cover(self, start: str | datetime | pd.Timestamp,
                     end: str | datetime | pd.Timestamp) -> bool:
        """Whether the observed first/last quotes bracket a requested interval.

        This deliberately checks bounds only; it does not claim there are no
        exchange halts or internal gaps inside the interval.
        """
        start_ts = _naive_timestamp(start, "coverage start")
        end_ts = _naive_timestamp(end, "coverage end")
        if end_ts < start_ts:
            raise ValueError("Coverage end precedes start")
        return self.first is not None and self.last is not None and self.first <= start_ts <= end_ts <= self.last


@dataclass(frozen=True, slots=True)
class QuoteDay:
    """Immutable, ordered executable NBBO arrays for one source trading date."""

    time: np.ndarray
    bid: np.ndarray
    ask: np.ndarray
    coverage: TickCoverage

    def __len__(self) -> int:
        return len(self.time)

    def window(self, start: str | datetime | pd.Timestamp,
               end: str | datetime | pd.Timestamp) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return immutable views for ``start <= time < end`` in strategy time."""
        start_ts = _naive_timestamp(start, "quote-window start")
        end_ts = _naive_timestamp(end, "quote-window end")
        if end_ts < start_ts:
            raise ValueError("Quote window end precedes start")
        left = int(np.searchsorted(self.time, start_ts.to_datetime64(), side="left"))
        right = int(np.searchsorted(self.time, end_ts.to_datetime64(), side="left"))
        return self.time[left:right], self.bid[left:right], self.ask[left:right]


def _coerce_source_date(value: str | date | datetime | pd.Timestamp) -> date:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid source date: {value!r}") from exc
    if pd.isna(stamp):
        raise ValueError(f"Invalid source date: {value!r}")
    return stamp.date()


def _quote_columns(path: Path) -> list[str]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("TickStore requires pyarrow; install orb-backtest[app]") from exc
    names = pq.ParquetFile(path).schema_arrow.names
    by_lower = {name.lower(): name for name in names}
    required = ("date", "time_m", "best_bid", "best_ask")
    missing = [name for name in required if name not in by_lower]
    if missing:
        raise ValueError(f"{path}: missing WRDS NBBO columns {missing}")
    wanted = list(required)
    if "time_m_nano" in by_lower:
        wanted.append("time_m_nano")
    sequence = next((name for name in _SEQUENCE_COLUMNS if name in by_lower), None)
    if sequence:
        wanted.append(sequence)
    return [by_lower[name] for name in wanted]


def _load_quote_day(path: Path, source_date: date, offset_hours: int) -> QuoteDay:
    columns = _quote_columns(path)
    raw = _normalise_columns(pd.read_parquet(path, columns=columns, engine="pyarrow"))
    source_rows = len(raw)
    if raw.empty:
        raise ValueError(f"{path}: quote file is empty")
    observed_dates = set(raw["date"].astype("string").str.strip().dropna().unique())
    if observed_dates != {source_date.isoformat()}:
        raise ValueError(f"{path}: expected source date {source_date}, found {sorted(observed_dates)}")

    stamps = _wrds_day_timestamps(raw, source_date)
    if offset_hours:
        stamps = stamps + pd.Timedelta(hours=offset_hours)
    valid_timestamp = stamps.notna().to_numpy()
    dropped_timestamps = int((~valid_timestamp).sum())
    raw = raw.loc[valid_timestamp]
    stamps = stamps.loc[valid_timestamp]
    bid = pd.to_numeric(raw["bid"], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    ask = pd.to_numeric(raw["ask"], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    finite_positive = np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > 0)
    crossed = finite_positive & (ask < bid)
    keep = finite_positive & ~crossed
    dropped_invalid = int((~finite_positive).sum())
    dropped_crossed = int(crossed.sum())

    times = stamps.to_numpy(dtype="datetime64[ns]")[keep]
    bids = bid[keep]
    asks = ask[keep]
    sequence = next((column for column in _SEQUENCE_COLUMNS if column in raw), None)
    if sequence:
        seq = pd.to_numeric(raw.loc[keep, sequence], errors="coerce").to_numpy(dtype=float)
        seq = np.where(np.isfinite(seq), seq, np.inf)
        order = np.lexsort((np.arange(len(times), dtype=np.int64), seq, times.view("int64")))
        times, bids, asks = times[order], bids[order], asks[order]
    elif len(times) > 1 and np.any(times[1:] < times[:-1]):
        order = np.argsort(times, kind="stable")
        times, bids, asks = times[order], bids[order], asks[order]

    duplicate_timestamps = int(pd.Index(times).duplicated(keep=False).sum())
    if duplicate_timestamps and sequence is None:
        adjacent = times[1:] == times[:-1]
        contradictory = adjacent & ((bids[1:] != bids[:-1]) | (asks[1:] != asks[:-1]))
        if contradictory.any():
            raise ValueError(f"{path}: contradictory NBBO states share a timestamp without a sequence key")
        # Byte-identical states at the same timestamp add no execution event.
        unique = np.r_[True, ~adjacent]
        times, bids, asks = times[unique], bids[unique], asks[unique]
    times = np.array(times, dtype="datetime64[ns]", copy=True)
    bids = np.array(bids, dtype=np.float64, copy=True)
    asks = np.array(asks, dtype=np.float64, copy=True)
    for array in (times, bids, asks):
        array.setflags(write=False)
    first = pd.Timestamp(times[0]) if len(times) else None
    last = pd.Timestamp(times[-1]) if len(times) else None
    coverage = TickCoverage(
        source_date=source_date,
        source_path=path,
        source_rows=source_rows,
        rows=len(times),
        dropped_timestamps=dropped_timestamps,
        dropped_invalid=dropped_invalid,
        dropped_crossed=dropped_crossed,
        duplicate_timestamps=duplicate_timestamps,
        first=first,
        last=last,
        strategy_offset_hours=offset_hours,
    )
    return QuoteDay(time=times, bid=bids, ask=asks, coverage=coverage)


class TickStore:
    """Lazy WRDS complete-NBBO reader with a bounded least-recently-used cache.

    Expected layout::

        root/2018/2018-03/QQQ_2018-03-01_quotes.parquet

    ``strategy_offset_hours`` has no default so a caller cannot accidentally mix
    New York source time with this project's naive strategy clock.
    """

    def __init__(self, root: str | Path, *, strategy_offset_hours: int,
                 symbol: str = "QQQ", max_cache_days: int = 2) -> None:
        self.root = Path(root).expanduser()
        if isinstance(strategy_offset_hours, bool) or not isinstance(strategy_offset_hours, int):
            raise TypeError("strategy_offset_hours must be an integer")
        if isinstance(max_cache_days, bool) or not isinstance(max_cache_days, int):
            raise TypeError("max_cache_days must be an integer")
        if max_cache_days < 0:
            raise ValueError("max_cache_days must be nonnegative")
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789." for character in normalized_symbol):
            raise ValueError("symbol must contain only A-Z, 0-9 or periods")
        self.strategy_offset_hours = strategy_offset_hours
        self.symbol = normalized_symbol
        self.max_cache_days = max_cache_days
        self._cache: OrderedDict[date, QuoteDay] = OrderedDict()
        self._coverage: dict[date, TickCoverage] = {}
        self._lock = RLock()

    @property
    def cached_dates(self) -> tuple[date, ...]:
        """Cached source dates in least-to-most-recently-used order."""
        with self._lock:
            return tuple(self._cache)

    @property
    def coverage_log(self) -> dict[date, TickCoverage]:
        """Coverage audits for every source day loaded during this process."""
        with self._lock:
            return dict(self._coverage)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def quote_path(self, source_date: str | date | datetime | pd.Timestamp) -> Path:
        day = _coerce_source_date(source_date)
        month = self.root / f"{day:%Y}" / f"{day:%Y-%m}"
        expected = month / f"{self.symbol}_{day.isoformat()}_quotes.parquet"
        if expected.is_file():
            return expected
        matches = sorted(month.glob(f"{self.symbol}_{day.isoformat()}_quotes.pq")) if month.is_dir() else []
        if len(matches) == 1:
            return matches[0]
        raise FileNotFoundError(f"WRDS NBBO quote file not found: {expected}")

    def load_day(self, source_date: str | date | datetime | pd.Timestamp) -> QuoteDay:
        """Load and validate exactly one source date, or return its cached value."""
        day = _coerce_source_date(source_date)
        with self._lock:
            cached = self._cache.pop(day, None)
            if cached is not None:
                self._cache[day] = cached
                return cached
            result = _load_quote_day(self.quote_path(day), day, self.strategy_offset_hours)
            self._coverage[day] = result.coverage
            if self.max_cache_days:
                self._cache[day] = result
                while len(self._cache) > self.max_cache_days:
                    self._cache.popitem(last=False)
            return result


def tick_quality(ticks: pd.DataFrame) -> dict[str, int | float | str]:
    """Small, deterministic audit used by the Streamlit tick overlay."""
    price_columns = [column for column in ("bid", "ask", "price") if column in ticks]
    numeric = ticks[price_columns].apply(pd.to_numeric, errors="coerce")
    matrix = numeric.to_numpy(dtype=float, na_value=np.nan)
    invalid = ~np.isfinite(matrix).all(axis=1) | (matrix <= 0).any(axis=1)
    crossed = (((pd.to_numeric(ticks.ask, errors="coerce")
                 < pd.to_numeric(ticks.bid, errors="coerce")).fillna(False))
               if {"bid", "ask"}.issubset(ticks.columns)
               else pd.Series(False, index=ticks.index))
    return {
        "rows": len(ticks),
        "first": str(ticks.index.min()),
        "last": str(ticks.index.max()),
        "duplicate_timestamps": int(ticks.index.duplicated(keep=False).sum()),
        "dropped_timestamps": int(ticks.attrs.get("dropped_timestamps", 0)),
        "invalid_prices": int(invalid.sum()),
        "crossed_quotes": int(crossed.sum()),
    }
