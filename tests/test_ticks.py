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


def test_compact_trade_tick_bytes():
    raw = b"time,price,size\n2025-01-01 09:30:00.1,100.5,10\n"
    ticks = load_ticks(raw, "ticks.csv")
    assert ticks.iloc[0].price == 100.5
    assert tick_quality(ticks)["invalid_prices"] == 0
