"""The two intraday momentum rules trade the right side at the right times, net of costs."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from orb_research.intraday_momentum import s1_last_half_hour, s2_noise_area, session_grid


def _day_bars(day: str, prices: dict[str, float], base: float) -> pd.DataFrame:
    """09:30-16:00 New York minute bars at `base`, with price steps from each HH:MM key on."""
    index = pd.date_range(f"{day} 09:30", f"{day} 16:00", freq="min", inclusive="left", tz="America/New_York")
    close = pd.Series(base, index=index)
    for hhmm, value in sorted(prices.items()):
        close[close.index >= pd.Timestamp(f"{day} {hhmm}", tz="America/New_York")] = value
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=index.tz_convert("UTC"))


def test_s1_follows_the_first_half_hour_and_pays_the_spread() -> None:
    m1 = pd.concat([_day_bars("2023-01-03", {}, 100.0),
                    _day_bars("2023-01-04", {"09:45": 101.0, "15:45": 103.0}, 100.0)])
    close, spread, _ = session_grid(m1, np.full(len(m1), 0.1))
    trades = s1_last_half_hour(close, spread, slip=0.0)
    # day 1 has no previous close; day 2 is up at 10:00, so long 15:30 -> 16:00
    assert list(trades.direction) == [1]
    assert trades.ret.iloc[0] == pytest.approx((103.0 - (101.0 + 0.1)) / 101.0)
    down = pd.concat([_day_bars("2023-01-03", {}, 100.0),
                      _day_bars("2023-01-04", {"09:45": 99.0, "15:45": 97.0}, 100.0)])
    close, spread, _ = session_grid(down, np.full(len(down), 0.1))
    short = s1_last_half_hour(close, spread, slip=0.0)
    assert list(short.direction) == [-1]
    assert short.ret.iloc[0] == pytest.approx((99.0 - (97.0 + 0.1)) / 99.0)


def test_s2_enters_a_breakout_of_the_noise_area_and_is_flat_at_the_close() -> None:
    days = pd.bdate_range("2023-01-02", periods=16)
    quiet = [_day_bars(d.date().isoformat(), {"10:00": 100.5, "11:00": 100.0}, 100.0) for d in days[:15]]
    # 14 quiet days give sigma = 0.5% at 10:30 and 0 elsewhere; day 16 breaks out at 11:30 and holds
    last = _day_bars(days[15].date().isoformat(), {"11:00": 102.0}, 100.0)
    m1 = pd.concat([*quiet, last])
    close, spread, first = session_grid(m1, np.zeros(len(m1)))
    daily = s2_noise_area(close, spread, first, slip=0.0)
    final = daily.iloc[-1]
    assert final.trades == 1 and final.ret == pytest.approx(0.0)  # long at 102 from 11:30, out at 102
    assert (daily.trades.iloc[:-1] == 0).all() or daily.index[0] > days[0].date()


def test_s2_records_the_days_lowest_marked_equity_for_daily_loss_limits() -> None:
    days = pd.bdate_range("2023-01-02", periods=16)
    quiet = [_day_bars(d.date().isoformat(), {}, 100.0) for d in days[:15]]
    # day 16: breakout long at 11:30 (102), dips to 99 at 13:10, recovers to 102 by the close
    last = _day_bars(days[15].date().isoformat(), {"11:00": 102.0, "13:10": 99.0, "13:20": 102.0}, 100.0)
    m1 = pd.concat([*quiet, last])
    close, spread, first = session_grid(m1, np.zeros(len(m1)))
    final = s2_noise_area(close, spread, first, slip=0.0).iloc[-1]
    assert final.ret == pytest.approx(0.0)
    assert final.worst == pytest.approx((99.0 - 102.0) / 102.0)
