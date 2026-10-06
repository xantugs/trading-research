"""Integrity, tied bins, and session-block dependence in descriptive analysis."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from orb_research.feature_analysis import (SHORTLIST, _spearman, make_bins,
                                           pairwise_table, prepare_events,
                                           session_block_bootstrap)
from orb_research.ml_features import FEATURE_COLUMNS
from orb_research.walk_forward import CANDIDATE_BUFFERS


def _events() -> pd.DataFrame:
    rows = []
    for session_number, date in enumerate(("2020-01-02", "2020-01-03")):
        for buffer in CANDIDATE_BUFFERS:
            row = {name: float(session_number) for name in FEATURE_COLUMNS}
            row.update({"event_id": f"{date}|{buffer}", "session_id": date, "date": date,
                        "year": 2020, "buffer": buffer,
                        "direction": "long" if session_number == 0 else "short",
                        "direction_sign": 1 if session_number == 0 else -1,
                        "signal_time_utc": f"{date}T14:00:00+00:00",
                        "entry_time_utc": f"{date}T14:00:00+00:00",
                        "exit_time_utc": f"{date}T18:00:00+00:00",
                        "final_R": -1.0 if session_number == 0 else 1.0,
                        "net_pnl": -10.0 if session_number == 0 else 10.0,
                        "MAE_R": 1.0, "MFE_R": 1.0,
                        "trade_positive": session_number})
            row["price_vs_ema20_atr"] = 2.0 + session_number
            rows.append(row)
    return pd.DataFrame(rows)


def test_aligned_features_leave_raw_input_unchanged_and_exclude_outcomes() -> None:
    source = _events()
    before = source.copy(deep=True)
    frame = prepare_events(source)
    pd.testing.assert_frame_equal(source, before)
    assert frame.loc[0, "trend_alignment_ema20"] == 2.0
    assert frame.loc[4, "trend_alignment_ema20"] == -3.0
    pairs = pairwise_table(frame)
    assert not set(pairs.feature_a).intersection({"final_R", "MAE_R", "MFE_R", "net_pnl"})
    assert not set(pairs.feature_b).intersection({"final_R", "MAE_R", "MFE_R", "net_pnl"})
    assert _spearman(pd.Series([0, 0, 1, 1]), pd.Series([2, 2, 4, 4])) == 1.0


def test_tied_values_use_fewer_bins_and_bootstrap_complete_sessions() -> None:
    frame = prepare_events(_events())
    bins = make_bins(frame)
    assert bins["minutes_since_or_end"]["actual_bins"] == 2
    assert set(bins["minutes_since_or_end"]["codes"].dropna().astype(int)) == {1, 2}
    bootstrap = session_block_bootstrap(frame, bins, samples=10_000, seed=17)
    selected = bootstrap.set_index("feature").loc["minutes_since_or_end"]
    assert selected.bootstrap_samples_valid > 0
    assert selected.bootstrap_samples_valid < 10_000  # all-A or all-B draws have no contrast
    assert np.isclose(selected.bootstrap_mean_effect, 2.0)
    assert np.isclose(selected.bootstrap_ci_low, 2.0)
    assert np.isclose(selected.bootstrap_ci_high, 2.0)
    assert selected.unique_sessions == 2
    assert selected.excluded_missing_rows == 0


def test_holdout_and_duplicate_event_guards() -> None:
    source = _events()
    future = source.copy()
    future.loc[0, "date"] = "2025-01-02"
    with pytest.raises(ValueError, match="2025"):
        prepare_events(future)
    duplicate = source.copy()
    duplicate.loc[1, "event_id"] = duplicate.loc[0, "event_id"]
    with pytest.raises(ValueError, match="unique"):
        prepare_events(duplicate)
    assert len(SHORTLIST) == 22
