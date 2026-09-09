import io

import numpy as np
import pandas as pd
import pytest

from orb.data import infer_minutes, load_bars, mt5_atr, normalise, resample
from orb.timezones import to_server_time


def sample():
    return pd.DataFrame({"open": [100, 101], "high": [102, 103], "low": [99, 100],
                         "close": [101, 102], "tick_volume": [1.5, 0], "spread": [100, 100]},
                        index=pd.date_range("2025-03-07 16:30", periods=2, freq="min"))


def test_csv_bytes_and_mt5_roundtrip(tmp_path):
    from fetch_dukascopy import write_mt5_format
    frame = sample()
    frame.index.name = "time"
    buf = io.StringIO()
    frame.to_csv(buf)
    loaded = load_bars(buf.getvalue().encode())
    pd.testing.assert_frame_equal(loaded, normalise(frame), check_freq=False)
    target = tmp_path / "mt5.csv"
    write_mt5_format(frame, target)
    loaded = load_bars(target)
    np.testing.assert_allclose(loaded[["open", "high", "low", "close"]], frame[["open", "high", "low", "close"]])
    assert (loaded.tick_volume == 1).all()


def test_gzip_csv_roundtrip(tmp_path):
    target = tmp_path / "QQQ_M1.csv.gz"
    frame = sample()
    frame.index.name = "time"
    frame.to_csv(target, compression="gzip")
    pd.testing.assert_frame_equal(load_bars(target), normalise(frame), check_freq=False)


@pytest.mark.parametrize("problem", ["nan", "negative", "envelope", "duplicate", "spread", "aware", "empty"])
def test_invalid_bars_fail(problem):
    frame = sample()
    if problem == "nan":
        frame.iloc[0, 0] = np.nan
    elif problem == "negative":
        frame.iloc[0, 0] = -1
    elif problem == "envelope":
        frame.iloc[0, 1] = 80
    elif problem == "duplicate":
        frame.index = pd.DatetimeIndex([frame.index[0]] * 2)
    elif problem == "spread":
        frame["spread"] = -1
    elif problem == "aware":
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame = frame.iloc[:0]
    with pytest.raises(ValueError):
        normalise(frame)


def test_clock_open_remains_1630_across_us_dst():
    utc = pd.DatetimeIndex(["2025-03-07 14:30", "2025-03-10 13:30",
                            "2025-10-31 13:30", "2025-11-03 14:30"])
    converted = to_server_time(utc, "ny+7")
    assert list(converted.strftime("%H:%M")) == ["16:30"] * 4
    assert list(to_server_time(utc, "+3").strftime("%H:%M")) == ["17:30", "16:30", "16:30", "17:30"]


def test_dst_transition_adds_wall_hours_not_elapsed_hours():
    utc = pd.DatetimeIndex(["2025-03-09 06:30", "2025-03-09 07:30"], tz="UTC")
    assert list(to_server_time(utc, "ny+7").strftime("%H:%M")) == ["08:30", "10:30"]
    fall = pd.DatetimeIndex(["2025-11-02 05:30", "2025-11-02 06:30"], tz="UTC")
    assert to_server_time(fall, "ny+7").has_duplicates  # downstream loader rejects ambiguous bars


def test_mt5_atr_is_simple_window_with_seed():
    frame = pd.DataFrame({"high": [10, 12, 14, 20, 15], "low": [8, 9, 10, 10, 10],
                          "close": [9, 10, 12, 12, 12]})
    atr = mt5_atr(frame, 2)
    assert atr.iloc[:2].isna().all()
    assert atr.iloc[2] == 3.5  # TR: 3,4
    assert atr.iloc[3] == 7    # TR: 4,10
    assert atr.iloc[4] == 7.5  # TR: 10,5; differs from Wilder


def test_resampling_never_fills_missing_prices():
    frame = sample()
    frame.index = pd.DatetimeIndex(["2025-03-07 16:30", "2025-03-07 17:00"])
    result = resample(frame, 15)
    assert len(result) == 2
    assert pd.Timestamp("2025-03-07 16:45") not in result.index
    ticks = sample()
    ticks.index = pd.date_range("2025-01-01", periods=2, freq="s")
    with pytest.raises(ValueError):
        infer_minutes(ticks)
