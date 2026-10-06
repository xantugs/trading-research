"""Clock and research-boundary invariants for the distinct true-NY strategy."""
from dataclasses import replace

import pandas as pd
import pytest

from orb_research.config import Config
from orb_research.data_loader import decode_broker_clock
from orb_research.walk_forward import validate_walk_forward
from scripts.verify_broker_clock import session_instant


@pytest.mark.parametrize("day,expected_utc", [
    ("2024-02-13", "2024-02-13 13:20:00+00:00"),  # EST
    ("2024-06-12", "2024-06-12 12:20:00+00:00"),  # EDT
    ("2024-03-12", "2024-03-12 12:20:00+00:00"),  # US/EU mismatch
    ("2024-11-01", "2024-11-01 12:20:00+00:00"),  # EU/US mismatch
])
def test_true_ny_session_start_and_exit(day, expected_utc):
    start = session_instant(day, "08:20")
    end = session_instant(day, "16:00")
    assert start == pd.Timestamp(expected_utc)
    assert start.tz_convert("America/New_York").strftime("%H:%M") == "08:20"
    assert end.tz_convert("America/New_York").strftime("%H:%M") == "16:00"
    assert end - start == pd.Timedelta(hours=7, minutes=40)


@pytest.mark.parametrize("day", ["2024-02-13", "2024-06-12", "2024-03-12", "2024-11-01"])
def test_decoder_and_legacy_are_distinct(day):
    # At 08:20 NY the cached FTMO wall clock reads 15:20, in every season.
    stored = pd.Timestamp(f"{day} 15:20", tz="UTC")
    bars = pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([stored], name="time"))
    legacy = decode_broker_clock(bars, Config(broker_clock="stored_as_utc"))
    corrected = decode_broker_clock(bars, Config(broker_clock="new_york_plus_7"))
    assert legacy.index[0] == stored
    assert corrected.index[0] == session_instant(day, "08:20")
    assert legacy.index[0] != corrected.index[0]


def test_walk_forward_rejects_final_holdout():
    config = replace(Config(), session_open="08:20", range_minutes=60)
    dates = pd.date_range("2024-12-31 23:59", periods=2, freq="min", tz="UTC")
    frame = pd.DataFrame({"close": [1, 2]}, index=dates)
    with pytest.raises(ValueError, match="final holdout"):
        validate_walk_forward(config, frame)
