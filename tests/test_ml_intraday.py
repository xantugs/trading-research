"""ML decision rows use only prices known at the checkpoint."""
from __future__ import annotations

import numpy as np
import pytest
import pandas as pd

from orb_research.intraday_momentum import session_grid
from orb_research.ml_intraday import FEATURES, decision_frame


def _random_days(seed: int, n: int = 40):
    rng = np.random.default_rng(seed)
    frames = []
    level = 100.0
    for day in pd.bdate_range("2023-01-02", periods=n):
        index = pd.date_range(f"{day.date()} 09:30", f"{day.date()} 16:00", freq="min", inclusive="left", tz="America/New_York")
        path = level * np.exp(np.cumsum(rng.normal(0, 0.0008, len(index))))
        level = path[-1]
        frames.append(pd.DataFrame({"open": path, "high": path, "low": path, "close": path}, index=index.tz_convert("UTC")))
    return pd.concat(frames)


def _utc_series(seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2023-01-01", "2023-03-31", freq="min", tz="UTC")
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.0005, len(index)))), index=index)


def _frame(m1, other):
    close, _, first = session_grid(m1, np.zeros(len(m1)))
    oclose, _, ofirst = session_grid(other, np.zeros(len(other)))
    return decision_frame(close, first, oclose, ofirst, _utc_series(3), _utc_series(4), 0)


def test_features_do_not_change_when_later_prices_change() -> None:
    m1, other = _random_days(1), _random_days(2)
    before = _frame(m1, other)
    last_day = m1.index.tz_convert("America/New_York").date[-1]
    later = m1.index >= pd.Timestamp(f"{last_day} 11:00", tz="America/New_York")
    changed = m1.copy()
    changed.loc[later, ["open", "high", "low", "close"]] *= 1.05      # rewrite the future after 11:00
    after = _frame(changed, other)
    rows = (before.date == last_day) & (before.check <= 2)            # checkpoints 10:00, 10:30, 11:00
    a = before.loc[rows, list(FEATURES)].to_numpy()
    b = after.loc[(after.date == last_day) & (after.check <= 2), list(FEATURES)].to_numpy()
    assert rows.sum() == 3
    np.testing.assert_allclose(a, b, equal_nan=True)
    # ... while the forward labels do see the change
    assert not np.allclose(before.loc[rows, "fwd_close"], after.loc[(after.date == last_day) & (after.check <= 2), "fwd_close"])


def test_direct_trades_close_at_the_last_price_of_a_half_day() -> None:
    from datetime import date

    from orb_research.ml_intraday import trade_direct

    day = date(2023, 11, 24)                          # the day after Thanksgiving closes at 13:00
    prices = np.full(390, np.nan)
    prices[:210] = 100.0                              # 09:30 ... 12:59
    prices[100:210] = 101.0                           # up from 11:10
    close = pd.DataFrame([prices], index=[day], columns=range(570, 960))
    spread = pd.DataFrame([np.zeros(390)], index=[day], columns=range(570, 960))
    rows = pd.DataFrame({"date": [day], "check": [0]})  # one decision at 10:00
    daily = trade_direct(rows, np.array([0.9]), 0.0, close, spread, slip=0.0)
    assert daily.trades.iloc[0] == 1
    assert daily.ret.iloc[0] == pytest.approx(0.01)   # long at 100, closed at the 12:59 close of 101
