"""Forward-test reconciliation: EA log parsing, trade matching and the pre-declared rules."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.forward_reconcile import evaluate, match, net_r, read_log

HEADER = ("date,direction,orb_time,orb_high,orb_low,orb_range,range_atr,vol_ratio,signal_time,entry_time,"
          "entry,sl,tp,lots,exit_time,exit,reason,r_multiple,profit,commission,swap,balance")


def _log(tmp_path, rows):
    path = tmp_path / "ORB_1_US100.cash.csv"
    path.write_text("\n".join([HEADER, "# ORB EA v1.30 | started 2026.10.08 08:00 | risk=0.50 tf", *rows]) + "\n",
                    encoding="utf-8")
    return path


def test_log_parses_broker_times_and_net_r(tmp_path) -> None:
    row = ("2026.10.08,long,2026.10.08 16:30,25010.00,24980.00,30.00,0.000,1.80,2026.10.08 17:00:00,"
           "2026.10.08 17:00:01,25012.00,24980.00,25060.00,1.50,2026.10.08 18:30:00,25060.00,TP,1.500,72.00,0.00,0.00,10072.00")
    live, settings = read_log(_log(tmp_path, [row]))
    assert settings and "risk=0.50" in settings[0]
    trade = live.iloc[0]
    # 17:00 broker is 10:00 New York (14:00 UTC in October)
    assert trade.entry_time == pd.Timestamp("2026-10-08 14:00:01", tz="UTC")
    assert (trade.session_date, trade.exit_reason) == ("2026-10-08", "target")
    assert net_r(live, 1.0).iloc[0] == pytest.approx(72 / (32 * 1.5))


def test_match_flags_missing_trades_both_ways() -> None:
    live = pd.DataFrame({"session_date": ["2026-10-08", "2026-10-09"], "direction": ["long", "short"],
                         "entry": [1.0, 2.0], "exit_reason": ["target", "stop"], "net_r": [1.5, -1.0]})
    engine = pd.DataFrame({"session_date": ["2026-10-08", "2026-10-12"], "direction": ["long", "long"],
                           "entry": [1.2, 3.0], "exit_reason": ["target", "stop"], "R_result": [1.4, -1.0]})
    merged = match(live, engine)
    assert dict(merged.status.value_counts()) == {"matched": 1, "live_only": 1, "engine_only": 1}


def _merged(r, entry_gap=0.5, agree=True):
    n = len(r)
    return pd.DataFrame({"status": ["matched"] * n, "live_entry": np.full(n, 100.0) + entry_gap,
                         "engine_entry": np.full(n, 100.0), "live_exit_reason": ["stop"] * n,
                         "engine_exit_reason": ["stop" if agree else "target"] * n, "live_net_r": r,
                         "live_exit_time": pd.date_range("2026-10-08", periods=n, freq="D", tz="UTC")})


def test_rules_follow_the_forward_test_plan() -> None:
    assert evaluate(_merged(np.full(20, 0.1)), 0.5)["performance"]["verdict"].startswith("CONTINUE: 20")
    # Alternating +1.5R / -1R averages +0.25R: passes the checkpoint, then counts as support.
    good = np.tile([1.5, -1.0], 75)
    assert evaluate(_merged(good[:60]), 0.5)["performance"]["verdict"].startswith("CONTINUE: passed")
    assert evaluate(_merged(good), 0.5)["performance"]["verdict"].startswith("SUPPORTED")
    # Mean -0.30R over 60 trades trips the checkpoint (risk kept tiny so drawdown does not).
    bad = np.tile([1.5, -1.0, -1.0, -1.0], 15) + 0.1
    assert evaluate(_merged(bad), 0.01)["performance"]["verdict"].startswith("STOP at the 60-trade")
    # 30 straight full losses at 0.5% risk is a 14% drawdown, beyond the backtest's worst 12.2%.
    assert evaluate(_merged(np.full(30, -1.0)), 0.5)["performance"]["verdict"].startswith("STOP: drawdown")
    fidelity = evaluate(_merged(np.full(10, 0.1), entry_gap=3.0), 0.5)["fidelity"]
    assert fidelity["pass"] is False and fidelity["median_entry_difference_points"] == pytest.approx(3.0)
    assert evaluate(_merged(np.full(10, 0.1)), 0.5)["fidelity"]["pass"] is True
