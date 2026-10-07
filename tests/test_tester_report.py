"""MT5-style tester report statistics and the dashboard presets that feed it."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from orb_research.tester_report import tester_report as build_report
from webapp.server import CONFIG, PRESETS, ResearchWeb, validate_parameters


def _ledger(pnls, start="2023-01-03 14:45", hours=2.0, direction="long"):
    rows = []
    for i, pnl in enumerate(pnls):
        entry = pd.Timestamp(start, tz="UTC") + pd.Timedelta(days=i)
        rows.append({"timestamp": entry, "exit_time": entry + pd.Timedelta(hours=hours),
                     "direction": direction, "PnL": pnl, "estimated_risk": 100.0,
                     "MAE": 0.5, "MFE": 1.0 if pnl > 0 else 0.2, "commission": 1.0})
    return pd.DataFrame(rows)


def test_profit_figures_streaks_and_drawdown() -> None:
    report = build_report(_ledger([100, 100, -50, -50, -50, 150]), 1000.0)
    s = report["stats"]
    assert s["total_net_profit"] == 200 and s["gross_profit"] == 350 and s["gross_loss"] == -150
    assert s["profit_factor"] == pytest.approx(350 / 150)
    assert s["expected_payoff"] == pytest.approx(200 / 6)
    assert (s["profit_trades"], s["loss_trades"], s["total_deals"]) == (3, 3, 12)
    assert s["max_consecutive_wins"] == {"count": 2, "money": 200}
    assert s["max_consecutive_losses"] == {"count": 3, "money": -150}
    assert s["average_consecutive_wins"] == 1.5 and s["average_consecutive_losses"] == 3
    # Balance 1000 → 1100 → 1200 → 1150 → 1100 → 1050 → 1200: the deepest fall is 150 from 1200.
    dd = s["balance_drawdown"]
    assert dd["absolute"] == 0 and dd["maximal"] == 150
    assert dd["maximal_pct"] == pytest.approx(12.5)
    assert s["recovery_factor"] == pytest.approx(200 / 150)
    assert s["ghpr"] == pytest.approx(1.2 ** (1 / 6))
    assert s["holding_time"] == {"min": "2:00:00", "max": "2:00:00", "average": "2:00:00"}


def test_runs_test_z_score_and_charts() -> None:
    # Perfect alternation is the opposite of streakiness: many runs, positive Z.
    report = build_report(_ledger([10, -10] * 10), 1000.0)
    s = report["stats"]
    expected = (20 * (20 - 0.5) - 200) / math.sqrt(200 * (200 - 20) / 19)
    assert s["z_score"] == pytest.approx(expected)
    assert 0 < s["z_confidence_pct"] < 100
    # Entries at 14:45 UTC in January are 09:45 New York.
    hours = {row["label"]: row["entries"] for row in report["by_hour"]}
    assert hours["09"] == 20 and sum(hours.values()) == 20
    assert [row["label"] for row in report["by_weekday"]] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert len(report["scatter"]) == 20 and report["scatter"][0]["mfe"] == 100.0
    assert report["scatter"][0]["mae"] == -50.0  # MAE shown negative, as in MT5
    assert len(report["balance_curve"]) == 21


def test_worst_case_equity_drawdown_counts_overlapping_positions() -> None:
    ledger = _ledger([50, 50], hours=50)  # both still open together on day two
    report = build_report(ledger, 1000.0)
    # At the second entry: balance 1000, both positions at MAE 0.5 × 100 = 50 each.
    assert report["stats"]["equity_drawdown_estimate"]["maximal"] == pytest.approx(100)


def test_empty_and_malformed_ledgers() -> None:
    assert build_report(_ledger([]).reindex(columns=_ledger([1]).columns), 1000.0) == {"trades": 0}
    with pytest.raises(ValueError, match="missing"):
        build_report(pd.DataFrame({"PnL": [1.0]}), 1000.0)


def test_every_preset_loads_and_round_trips_through_validation(tmp_path) -> None:
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    listed = app.preset_list()
    assert [preset["id"] for preset in listed] == [spec["id"] for spec in PRESETS]
    assert app.default_preset == "us100_candidate"
    assert app.presets["us100_candidate"][1].risk_percent == 0.5
    assert app.presets["us100_candidate"][1].symbol == "US100.cash"
    for preset in listed:
        _, config = app.presets[preset["id"]]
        assert validate_parameters(config, preset["parameters"])["broker_clock"] == preset["window"]["broker_clock"]
    clocks = {preset["id"]: preset["window"]["broker_clock"] for preset in listed}
    assert clocks.pop("legacy_research") == "stored_as_utc"
    assert set(clocks.values()) == {"new_york_plus_7"}
    assert app.presets["orb_framework_15"][1].signal_minutes == 15
    with pytest.raises(ValueError, match="preset"):
        app.submit({}, False, "no_such_preset")


def test_tick_ledgers_with_mixed_fractional_seconds_parse(tmp_path) -> None:
    ledger = _ledger([10, -5])
    path = tmp_path / "trades.csv"
    ledger.assign(timestamp=["2023-01-03 14:45:00.123+00:00", "2023-01-04 14:45:07+00:00"]).to_csv(path, index=False)
    assert build_report(pd.read_csv(path), 1000.0)["trades"] == 2
