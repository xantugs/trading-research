import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from scripts.wrds_build_m1_local import OUTPUT_COLUMNS, build, build_day, discover_pairs, sha256


def write_raw_day(root, day="2020-01-02"):
    month = root / day[:4] / day[:7]
    month.mkdir(parents=True, exist_ok=True)
    trades = month / f"QQQ_{day}_trades.parquet"
    quotes = month / f"QQQ_{day}_quotes.parquet"
    pd.DataFrame({
        "date": [day] * 5,
        "time_m": ["09:30:00.100000", "09:30:00.100000", "09:30:30", "09:30:40", "09:31:00"],
        "time_m_nano": [500, 100, 0, 0, 0],
        "price": [200.0, 199.0, 201.0, 110.0, 202.0],
        "size": [10, 20, 30, 100, 5],
        "tr_corr": ["00", "00", "00", "01", "01"],
        "tr_seqnum": [2, 1, 3, 4, 5],
    }).to_parquet(trades, index=False)
    pd.DataFrame({
        "date": [day] * 5,
        "time_m": ["09:30:00.100000", "09:30:00.100000", "09:30:30", "09:30:40", "09:31:00"],
        "time_m_nano": [500, 100, 0, 0, 0],
        "best_bid": [100.0, 99.0, 101.0, 100.0, 101.96],
        "best_ask": [100.01, 99.02, 101.03, 99.99, 102.0],
    }).to_parquet(quotes, index=False)
    return trades, quotes


def test_build_day_matches_cloud_nbbo_ohlc_trade_volume_and_ny_plus_7(tmp_path):
    trades, quotes = write_raw_day(tmp_path)
    bars, record = build_day(
        trades, quotes, date(2020, 1, 2), point=0.01,
        start_time="09:30:00", end_time="09:32:00",
    )

    assert list(bars.time) == [pd.Timestamp("2020-01-02 16:30"), pd.Timestamp("2020-01-02 16:31")]
    assert list(bars.columns) == OUTPUT_COLUMNS
    first = bars.iloc[0]
    assert (first.open, first.high, first.low, first.close) == (99.0, 101.0, 99.0, 101.0)
    assert first.real_volume == 60
    assert first.tick_volume == 3
    assert first.quote_updates == 3
    assert first.spread == 3
    assert np.isclose(first.spread_median, 0.02)
    assert np.isclose(first.spread_p95, 0.029)
    assert bars.iloc[1].tick_volume == bars.iloc[1].real_volume == 0
    assert record["trade_count"] == 3
    assert record["quote_updates"] == 4


def test_build_writes_month_and_year_outputs_with_manifests(tmp_path):
    raw = tmp_path / "raw"
    write_raw_day(raw, "2020-01-02")
    write_raw_day(raw, "2020-01-03")
    output = tmp_path / "m1"

    result = build(raw, output, start_time="09:30:00", end_time="09:32:00")

    month = output / "monthly" / "2020" / "2020-01" / "QQQ_2020-01_M1.csv.gz"
    year = output / "years" / "2020" / "QQQ_2020-01-01_2021-01-01_M1.csv.gz"
    assert month.is_file() and year.is_file()
    assert len(pd.read_csv(month)) == len(pd.read_csv(year)) == 4
    month_manifest = json.loads((month.parent / "manifest.json").read_text())
    year_manifest = json.loads((year.parent / "manifest.json").read_text())
    top = json.loads((output / "manifest.json").read_text())
    assert month_manifest["tick_volume_basis"].startswith("positive correction-code 00")
    assert len(month_manifest["days"]) == len(year_manifest["days"]) == 2
    assert top == result
    assert top["months"] == 1 and top["trading_days"] == 2 and top["rows"] == 4

    second = tmp_path / "m1-repeat"
    build(raw, second, start_time="09:30:00", end_time="09:32:00")
    repeat = second / "years" / "2020" / year.name
    assert sha256(year) == sha256(repeat)


def test_discover_pairs_rejects_partial_download(tmp_path):
    month = tmp_path / "2020" / "2020-01"
    month.mkdir(parents=True)
    pd.DataFrame({"x": [1]}).to_parquet(month / "QQQ_2020-01-02_trades.parquet")
    with pytest.raises(ValueError, match="missing quotes"):
        discover_pairs(tmp_path, "QQQ")
