"""The three 0DTE-era rules trade the intended side at the intended times."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from orb_research.intraday_momentum import session_grid
from orb_research.regime_strategies import r2_calm_fade, r3_late_reversal, r4_overnight_long
from tests.test_intraday_momentum import _day_bars


def _grid(days):
    m1 = pd.concat(days)
    return session_grid(m1, np.zeros(len(m1)))


def test_r3_fades_a_large_move_from_15_00_to_the_close() -> None:
    dates = [d.date().isoformat() for d in pd.bdate_range("2023-01-02", periods=16)]
    quiet = [_day_bars(d, {"14:00": 100.2, "15:30": 100.0}, 100.0) for d in dates[:15]]   # +0.2% at 15:00
    last = _day_bars(dates[15], {"14:00": 102.0, "15:30": 101.0}, 100.0)                    # +2% at 15:00
    close, spread, first = _grid([*quiet, last])
    final = r3_late_reversal(close, spread, first, slip=0.0).iloc[-1]
    assert final.trades == 1 and final.ret == pytest.approx(1.0 / 102.0)                    # short 102 -> 101


def test_r2_fades_back_to_the_open_only_on_calm_days() -> None:
    dates = [d.date().isoformat() for d in pd.bdate_range("2023-01-02", periods=25)]
    quiet = [_day_bars(d, {"10:00": 100.2, "11:00": 100.0}, 100.0) for d in dates[:24]]
    last = _day_bars(dates[24], {"10:15": 101.0, "12:00": 99.9}, 100.0)   # 1% up at 10:30, back below the open
    close, spread, first = _grid([*quiet, last])
    final = r2_calm_fade(close, spread, first, slip=0.0).iloc[-1]
    assert final.trades == 1 and final.ret == pytest.approx((101.0 - 99.9) / 101.0)


def test_r4_holds_overnight_and_pays_three_nights_of_swap_over_a_weekend() -> None:
    friday = _day_bars("2023-01-06", {}, 100.0)
    monday = _day_bars("2023-01-09", {"09:30": 101.0}, 101.0)
    close, spread, first = _grid([friday, monday])
    trade = r4_overnight_long(close, spread, first, swap_per_night=0.0002, slip=0.0).iloc[0]
    assert trade.ret == pytest.approx(0.01 - 3 * 0.0002)
