"""Month-cached MT5 M1 bars with explicit UTC timestamps and broker provenance."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config


BAR_COLUMNS = ("open", "high", "low", "close", "tick_volume", "spread", "real_volume")


def _month_bounds(start: str, end: str):
    start_date = pd.Timestamp(start, tz="UTC")
    left = pd.Timestamp(year=start_date.year, month=start_date.month, day=1, tz="UTC")
    stop = pd.Timestamp(end, tz="UTC")
    while left < stop:
        right = left + pd.offsets.MonthBegin(1)
        yield left, right
        left = right


def validate_m1_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate a raw M1 partition without applying any research date split."""
    if frame.empty:
        raise ValueError("M1 dataset is empty")
    if not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != "UTC":
        raise ValueError("M1 timestamps must have an explicit UTC timezone")
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("M1 timestamps must be unique and ordered")
    if (frame.index.second != 0).any() or (frame.index.microsecond != 0).any():
        raise ValueError("M1 timestamps must begin exactly on a minute")
    for column in BAR_COLUMNS:
        if column not in frame:
            raise ValueError(f"M1 data is missing {column}")
        if not np.isfinite(frame[column].to_numpy()).all():
            raise ValueError(f"non-finite M1 {column}")
    prices = frame[["open", "high", "low", "close"]]
    if (prices <= 0).any().any():
        raise ValueError("M1 prices must be positive")
    if (frame.high < prices.max(axis=1)).any() or (frame.low > prices.min(axis=1)).any():
        raise ValueError("invalid M1 OHLC envelope")
    if (frame.spread < 0).any() or (frame.tick_volume < 0).any():
        raise ValueError("negative M1 spread or volume")
    return frame


def decode_broker_clock(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    """Re-label cached bar timestamps according to the configured broker clock.

    MT5's Python API returns bar times as epoch seconds that carry the *server*
    clock; the cache stores them labelled as UTC.  For FTMO-Demo XAUUSD every
    08:30 New York release prints at 15:30 in that stored clock in winter and
    summer alike, so the server runs at New York + 7 h year-round.  Decoding
    subtracts seven hours and localises the result as America/New_York.  The
    default keeps the historical stored-as-UTC interpretation unchanged.
    """
    if config.broker_clock == "stored_as_utc" or frame.empty:
        return frame
    # Trading is closed during the Sunday DST transitions. If this ever changes,
    # fail rather than silently guessing or shifting a market-data timestamp.
    if config.broker_clock == "eastern_european":
        # MOTCapital-Live-1: UTC+2 in winter, UTC+3 in summer, switching on the EU dates.
        # The 09:30 New York open prints at 16:30 server time except in the weeks when
        # only one of the US and EU has changed clocks (March: 15:30).
        decoded = frame.index.tz_convert(None).tz_localize(
            "Europe/Athens", ambiguous="raise", nonexistent="raise").tz_convert("UTC")
    else:
        stored = frame.index.tz_convert(None) - pd.Timedelta(hours=7)
        decoded = stored.tz_localize("America/New_York", ambiguous="raise",
                                     nonexistent="raise").tz_convert("UTC")
    out = frame.copy()
    out.index = pd.DatetimeIndex(decoded, name=frame.index.name)
    return out.sort_index()


def validate_m1(frame: pd.DataFrame, config: Config) -> pd.DataFrame:
    validate_m1_bars(frame)
    start = pd.Timestamp(config.start, tz="UTC")
    end = pd.Timestamp(config.development_end, tz="UTC")
    subset = frame.loc[(frame.index >= start) & (frame.index < end)]
    if subset.empty:
        raise ValueError("no M1 bars inside the development period")
    return subset


def _from_mt5_array(rates) -> pd.DataFrame:
    frame = pd.DataFrame(rates)
    if frame.empty:
        return pd.DataFrame(columns=BAR_COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    frame["time"] = pd.to_datetime(frame.time, unit="s", utc=True)
    frame = frame.set_index("time").sort_index()
    frame.index.name = "time"
    return frame[list(BAR_COLUMNS)]


def _inspect_terminal(mt5, config: Config, *, prices_only: bool = False) -> dict:
    account = mt5.account_info()
    terminal = mt5.terminal_info()
    if account is None or terminal is None or not terminal.connected:
        raise RuntimeError("MT5 terminal is not connected to an account")
    if account.server != config.expected_server:
        raise RuntimeError(
            f"connected to {account.server!r}, expected {config.expected_server!r}; "
            "switch accounts or update the configuration deliberately"
        )
    if not mt5.symbol_select(config.symbol, True):
        raise RuntimeError(f"MT5 symbol {config.symbol!r} is unavailable: {mt5.last_error()}")
    info = mt5.symbol_info(config.symbol)
    if info is None:
        raise RuntimeError(f"MT5 symbol metadata unavailable: {mt5.last_error()}")
    if not np.isclose(info.point, config.point) or not np.isclose(info.trade_contract_size, config.contract_size):
        raise RuntimeError(
            f"symbol specification mismatch: broker point={info.point}, "
            f"contract={info.trade_contract_size}; config point={config.point}, "
            f"contract={config.contract_size}"
        )
    if (not np.isclose(info.volume_min, config.min_lot) or
            not np.isclose(info.volume_step, config.lot_step) or
            config.max_lot > info.volume_max):
        raise RuntimeError(
            f"volume specification mismatch: broker min/step/max="
            f"{info.volume_min}/{info.volume_step}/{info.volume_max}; config="
            f"{config.min_lot}/{config.lot_step}/{config.max_lot}"
        )
    if info.chart_mode != mt5.SYMBOL_CHART_MODE_BID:
        raise RuntimeError(f"research engine requires Bid-based {config.symbol} bars; broker chart mode is Last")
    # Price-only caches (studies that measure returns in % of price) may hold non-USD symbols;
    # the ORB engine's money sizing needs USD P/L, so it never reads them without this check.
    if not prices_only and (account.currency != "USD" or info.currency_profit != "USD"):
        raise RuntimeError("research sizing requires a USD account and USD symbol P/L")
    expected_tick_value = info.trade_tick_size * info.trade_contract_size
    if not prices_only and not np.isclose(info.trade_tick_value, expected_tick_value, rtol=0.01):
        raise RuntimeError(
            f"broker tick value {info.trade_tick_value} does not match "
            f"tick size x contract size {expected_tick_value}"
        )
    return {
        "company": account.company, "server": account.server, "symbol": info.name,
        "point": info.point, "contract_size": info.trade_contract_size,
        "tick_size": info.trade_tick_size, "tick_value": info.trade_tick_value,
        "volume_min": info.volume_min, "volume_step": info.volume_step,
        "volume_max": info.volume_max, "terminal_path": terminal.path,
        "chart_mode": "bid", "prices_only": prices_only, "profit_currency": info.currency_profit,
        "clock": "MT5 epoch seconds as delivered (server clock, labelled UTC; see broker_clock)",
    }


def load_m1(config: Config, *, refresh: bool = False, terminal_path: str | None = None):
    """Read cached M1 months; download missing months only from the exact server."""
    if config.source == "parquet":
        path = Path(config.data_path)
        frame = pd.read_parquet(path)
        provenance = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if (provenance.get("server") != config.expected_server or
                provenance.get("symbol") != config.symbol or
                provenance.get("chart_mode") != "bid"):
            raise ValueError("Parquet provenance does not match configured server and symbol")
        return validate_m1(decode_broker_clock(frame, config), config), provenance

    cache = Path(config.cache_dir) / config.expected_server / config.symbol
    cache.mkdir(parents=True, exist_ok=True)
    months = list(_month_bounds(config.start, config.development_end))
    missing = [left for left, _ in months if refresh or not (cache / f"{left:%Y%m}.parquet").exists()]
    metadata_path = cache / "source.json"
    if not missing and not metadata_path.exists():
        raise ValueError("cached months have no source.json; refuse unverified market data")

    if missing:
        try:
            import MetaTrader5 as mt5  # type: ignore
        except ImportError as exc:
            raise RuntimeError('install the MT5 extra: pip install -e ".[research-v2]"') from exc
        ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
        if not ok:
            raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
        try:
            provenance = _inspect_terminal(mt5, config)
            for left, right in months:
                target = cache / f"{left:%Y%m}.parquet"
                if target.exists() and not refresh:
                    continue
                rates = mt5.copy_rates_range(config.symbol, mt5.TIMEFRAME_M1,
                                             left.to_pydatetime(), right.to_pydatetime())
                if rates is None:
                    raise RuntimeError(f"MT5 M1 request failed for {left:%Y-%m}: {mt5.last_error()}")
                frame = _from_mt5_array(rates)
                frame = frame.loc[(frame.index >= left) & (frame.index < right)]
                if frame.empty:
                    raise RuntimeError(f"MT5 returned no M1 bars for {left:%Y-%m}; stop before analysis")
                request_start = max(left, pd.Timestamp(config.start, tz="UTC"))
                request_end = min(right, pd.Timestamp(config.development_end, tz="UTC"))
                expected_weekdays = pd.date_range(request_start, request_end, freq="D", inclusive="left")
                expected_count = int((expected_weekdays.weekday < 5).sum())
                observed_days = pd.Index(frame.index.normalize().unique())
                covered = sum(day in observed_days for day in expected_weekdays if day.weekday() < 5)
                if expected_count >= 5 and covered / expected_count < 0.8:
                    raise RuntimeError(
                        f"{left:%Y-%m} has M1 bars on only {covered}/{expected_count} weekdays; "
                        "verify broker retention and Max bars in chart"
                    )
                frame.to_parquet(target)
                print(f"M1 {left:%Y-%m}: {len(frame):,} bars -> {target}", flush=True)
            metadata_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        finally:
            mt5.shutdown()

    provenance = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (provenance.get("server") != config.expected_server or
            provenance.get("symbol") != config.symbol or
            provenance.get("chart_mode") != "bid"):
        raise ValueError("cached source identity changed")
    frames = [pd.read_parquet(cache / f"{left:%Y%m}.parquet") for left, _ in months]
    # A recorded broker-gap month is an empty, untyped file; it adds no bars.
    frames = [frame for frame in frames if not frame.empty]
    frame = pd.concat(frames).sort_index()
    frame = frame.loc[~frame.index.duplicated(keep="first")]
    return validate_m1(decode_broker_clock(frame, config), config), provenance
