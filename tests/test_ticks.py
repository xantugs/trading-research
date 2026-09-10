import pandas as pd
import pytest

from orb import load_ticks, tick_quality
from orb.ticks import TickStore


def write_quotes(root, day, rows=None):
    month = root / day[:4] / day[:7]
    month.mkdir(parents=True, exist_ok=True)
    path = month / f"QQQ_{day}_quotes.parquet"
    frame = pd.DataFrame(rows or {
        "date": [day], "time_m": pd.to_timedelta(["09:30:00"]), "time_m_nano": [0],
        "qu_seqnum": [1], "best_bid": [100.0], "best_ask": [100.01],
    })
    frame.to_parquet(path, index=False)
    return path


def test_wrds_quote_tick_layout(tmp_path):
    path = tmp_path / "QQQ_quotes.csv.gz"
    pd.DataFrame({
        "date": ["2024-03-08", "2024-03-08"],
        "time_m": ["09:30:00.001", "09:30:00.002"],
        "best_bid": [438.10, 438.11], "best_ask": [438.12, 438.10],
    }).to_csv(path, index=False, compression="gzip")
    ticks = load_ticks(path)
    assert list(ticks.columns[:2]) == ["bid", "ask"]
    assert ticks.index[0] == pd.Timestamp("2024-03-08 09:30:00.001")
    quality = tick_quality(ticks)
    assert quality["rows"] == 2
    assert quality["crossed_quotes"] == 1
    assert quality["dropped_timestamps"] == 0


def test_compact_trade_tick_bytes():
    raw = b"time,price,size\n2025-01-01 09:30:00.1,100.5,10\n"
    ticks = load_ticks(raw, "ticks.csv")
    assert ticks.iloc[0].price == 100.5
    assert tick_quality(ticks)["invalid_prices"] == 0


def test_wrds_nanoseconds_and_strategy_clock_are_preserved(tmp_path):
    path = tmp_path / "QQQ_trades.parquet"
    pd.DataFrame({
        "date": ["2018-03-01", "2018-03-01"],
        "time_m": ["09:30:00.123456", "09:30:00.123456"],
        "time_m_nano": [900, 100], "tr_seqnum": [2, 1],
        "price": [100.02, 100.01],
    }).to_parquet(path)
    ticks = load_ticks(path, strategy_offset_hours=7)
    assert list(ticks.price) == [100.01, 100.02]
    assert ticks.index[0] == pd.Timestamp("2018-03-01 16:30:00.123456100")
    assert ticks.index[1] == pd.Timestamp("2018-03-01 16:30:00.123456900")


def test_nullable_missing_quote_is_counted_invalid(tmp_path):
    path = tmp_path / "quotes.parquet"
    pd.DataFrame({
        "date": ["2018-01-02", None], "time_m": ["09:30:00", None],
        "best_bid": pd.Series([pd.NA, 100.0], dtype="Float64"),
        "best_ask": pd.Series([100.01, 100.01], dtype="Float64"),
    }).to_parquet(path)
    ticks = load_ticks(path)
    quality = tick_quality(ticks)
    assert quality["rows"] == 1
    assert quality["dropped_timestamps"] == 1
    assert quality["invalid_prices"] == 1


def test_tick_store_orders_filters_offsets_and_projects_columns(tmp_path, monkeypatch):
    path = write_quotes(tmp_path, "2018-03-01", {
        "date": ["2018-03-01"] * 5 + [None],
        "time_m": ["09:30:00.123456"] * 6,
        "time_m_nano": [900, 100, 100, 200, 300, 400],
        "qu_seqnum": [3, 2, 1, 4, 5, 6],
        "best_bid": [100.09, 100.01, 100.00, 0.0, 100.05, 100.06],
        "best_ask": [100.10, 100.02, 100.01, 100.01, 100.04, 100.07],
        "unused_payload": ["must not be read"] * 6,
    })
    real_read_parquet = pd.read_parquet
    observed = {}

    def spy_read_parquet(*args, **kwargs):
        observed["columns"] = kwargs.get("columns")
        return real_read_parquet(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", spy_read_parquet)
    store = TickStore(tmp_path, strategy_offset_hours=7, max_cache_days=1)
    quotes = store.load_day("2018-03-01")

    assert quotes.coverage.source_path == path
    assert observed["columns"] == [
        "date", "time_m", "best_bid", "best_ask", "time_m_nano", "qu_seqnum",
    ]
    assert list(quotes.bid) == [100.00, 100.01, 100.09]
    assert list(quotes.ask) == [100.01, 100.02, 100.10]
    assert list(quotes.time) == [
        pd.Timestamp("2018-03-01 16:30:00.123456100").to_datetime64(),
        pd.Timestamp("2018-03-01 16:30:00.123456100").to_datetime64(),
        pd.Timestamp("2018-03-01 16:30:00.123456900").to_datetime64(),
    ]
    assert quotes.coverage.source_rows == 6
    assert quotes.coverage.rows == 3
    assert quotes.coverage.dropped_timestamps == 1
    assert quotes.coverage.dropped_invalid == 1
    assert quotes.coverage.dropped_crossed == 1
    assert quotes.coverage.dropped_rows == 3
    assert quotes.coverage.duplicate_timestamps == 2
    assert quotes.coverage.bounds_cover(quotes.coverage.first, quotes.coverage.last)
    time, bid, ask = quotes.window(
        "2018-03-01 16:30:00.123456100", "2018-03-01 16:30:00.123456900",
    )
    assert len(time) == len(bid) == len(ask) == 2
    assert not quotes.time.flags.writeable and not quotes.bid.flags.writeable and not quotes.ask.flags.writeable
    with pytest.raises(ValueError):
        quotes.bid[0] = 0


def test_tick_store_cache_is_bounded_and_lru(tmp_path):
    for day in ("2018-03-01", "2018-03-02", "2018-03-05"):
        write_quotes(tmp_path, day)
    store = TickStore(tmp_path, strategy_offset_hours=7, max_cache_days=2)

    first = store.load_day("2018-03-01")
    second = store.load_day("2018-03-02")
    assert store.load_day("2018-03-01") is first
    assert store.cached_dates == (pd.Timestamp("2018-03-02").date(), pd.Timestamp("2018-03-01").date())
    store.load_day("2018-03-05")
    assert store.cached_dates == (pd.Timestamp("2018-03-01").date(), pd.Timestamp("2018-03-05").date())
    assert store.load_day("2018-03-02") is not second
    assert len(store.cached_dates) == 2
    store.clear()
    assert store.cached_dates == ()


def test_tick_store_requires_clock_and_reports_missing_day(tmp_path):
    with pytest.raises(TypeError):
        TickStore(tmp_path)
    store = TickStore(tmp_path, strategy_offset_hours=7, max_cache_days=0)
    with pytest.raises(FileNotFoundError, match="QQQ_2018-03-01_quotes.parquet"):
        store.load_day("2018-03-01")
    assert store.cached_dates == ()


def test_tick_store_never_falls_back_to_a_sibling_m1_root(tmp_path):
    tick_root = tmp_path / "wrds_ticks"
    write_quotes(tmp_path / "wrds_m1", "2018-03-01")
    store = TickStore(tick_root, strategy_offset_hours=7)

    with pytest.raises(FileNotFoundError) as error:
        store.load_day("2018-03-01")
    assert str(tick_root) in str(error.value)
