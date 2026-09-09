import pandas as pd

from orb import load_ticks, tick_quality


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
