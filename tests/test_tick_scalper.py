"""Tick-level mechanics of the ORB scalping EA simulator, on synthetic bid/ask ticks."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from orb_research.tick_scalper import (ScalperConfig, TickScalper, cached_tick_loader, ledger,
                                       load_scalper_config, to_utc)

DAY = date(2023, 3, 8)  # a Wednesday; broker clock = New York + 7 h
BASE = replace(load_scalper_config("configs/tick_scalper.json"), start="2023-03-08",
               development_end="2023-03-11", commission_rate_pct_side=0.0)
SPREAD = 0.25  # prices on a 0.25 grid are exact in binary, so level hits are exact


def _ms(clock: str, day: date = DAY) -> int:
    """Stored broker-clock epoch ms for a broker wall time such as '16:40:00.000'."""
    return int(pd.Timestamp(datetime.combine(day, datetime.strptime(clock, "%H:%M:%S.%f").time()),
                            tz="UTC").value // 1_000_000)


def _ticks(path: list[tuple[str, float]], day: date = DAY, step_ms: int = 100) -> pd.DataFrame:
    """Piecewise-constant bid path: each (broker time, bid) holds until the next, ticking every 100 ms.

    The range candle 16:15-16:30 oscillates between 2000.00 and 2001.00.
    """
    start, end = _ms("16:00:00.000", day), _ms("20:00:00.000", day)
    times = np.arange(start, end, step_ms)
    marks = np.array([_ms(clock, day) for clock, _ in path] if path else [], dtype=np.int64)
    levels = np.array([2000.50] + [bid for _, bid in path])
    bids = levels[np.searchsorted(marks, times, side="right")]
    in_range = (times >= _ms("16:15:00.000", day)) & (times < _ms("16:30:00.000", day))
    bids = np.where(in_range, np.where((times // 1000) % 2 == 1, 2000.0, 2001.0), bids)
    frame = pd.DataFrame({"time_msc": times, "bid": bids})
    frame["ask"] = frame.bid + SPREAD
    return frame


def _run(path, config=BASE, days=None):
    data = {DAY: _ticks(path)} if days is None else days
    sim = TickScalper(config, lambda day: data.get(day))
    trades, counts = sim.run()
    return (ledger(trades) if not trades.empty else trades), counts


def test_buy_stop_triggers_on_the_ask_and_fills_after_the_delay() -> None:
    # Ask reaches 2001.00 (the range high) when the bid steps to 2000.75 at 16:40.
    trades, counts = _run([("16:40:00.000", 2000.75), ("16:40:00.500", 2001.00),
                           ("16:41:00.000", 2003.50)], replace(BASE, trade_direction="long"))
    trade = trades.iloc[0]
    assert trade.signal_open == to_utc(_ms("16:40:00.000"))
    assert trade.timestamp == to_utc(_ms("16:40:00.200"))  # first tick >= trigger + 130 ms
    assert trade.entry == 2001.00  # the ask on the delayed fill tick
    assert (trade.range_high, trade.range_low) == (2001.0, 2000.0)
    assert trade.timestamp.tz_convert("America/New_York").strftime("%H:%M") == "09:40"


def test_tight_trailing_stop_locks_profit_tick_by_tick() -> None:
    # Long fills at 2001.00; the bid climbs to 2001.75, so the 10-point (0.10) trail sits at
    # 2001.65; the 16:43 bid of 2001.50 hits it and the exit fills 130 ms later on the bid.
    trades, _ = _run([("16:40:00.000", 2000.75), ("16:41:00.000", 2001.50),
                      ("16:42:00.000", 2001.75), ("16:43:00.000", 2001.50),
                      ("16:43:00.300", 2001.25)], replace(BASE, trade_direction="long"))
    trade = trades.iloc[0]
    assert trade.exit_reason == "trail_stop"
    assert trade.exit_time == to_utc(_ms("16:43:00.200"))
    assert trade.exit == 2001.50
    assert trade.PnL == pytest.approx(50.0) and trade.MFE == pytest.approx(0.75 / 2.00)


def test_initial_stop_and_fixed_target() -> None:
    long_only = replace(BASE, trade_direction="long")
    stopped, _ = _run([("16:40:00.000", 2000.75), ("16:41:00.000", 1998.75)], long_only)
    assert stopped.iloc[0].exit_reason == "stop" and stopped.iloc[0].R_result < -0.95
    # With trailing out of reach, a jump through +200 points fills the target as a limit.
    far = replace(long_only, trail_start_points=10_000)
    hit, _ = _run([("16:40:00.000", 2000.75), ("16:41:00.000", 2003.50)], far)
    assert hit.iloc[0].exit_reason == "target" and hit.iloc[0].exit == pytest.approx(2003.00)


def test_both_orders_live_unless_one_cancels_other() -> None:
    path = [("16:40:00.000", 2000.75), ("16:41:00.000", 2001.50), ("16:42:00.000", 2001.25),
            ("16:50:00.000", 1999.75), ("16:51:00.000", 1999.25), ("16:52:00.000", 1999.50)]
    both, _ = _run(path)
    assert list(both.direction) == ["long", "short"]
    single, counts = _run(path, replace(BASE, one_cancels_other=True))
    assert list(single.direction) == ["long"] and counts["cancelled_by_oco"] == 1


def test_order_inside_the_spread_is_rejected_and_cutoff_removes_orders() -> None:
    # At 16:30 the bid sits at 2000.75, so the ask (2001.00) is already at the buy stop.
    inside = [("16:30:00.000", 2000.75), ("16:31:00.000", 2001.50), ("16:35:00.000", 2001.00)]
    _, counts = _run(inside, replace(BASE, trade_direction="long"))
    assert counts["buy_stop_rejected"] == 1
    allowed, _ = _run(inside, replace(BASE, trade_direction="long", reject_inside_spread=False))
    assert len(allowed) == 1
    # A breakout after 19:00 broker (12:00 New York) finds no order.
    late, counts = _run([("19:00:00.000", 2001.50)], replace(BASE, trade_direction="long"))
    assert late.empty and counts["no_trigger"] == 1


def test_risk_sizing_and_margin_cap() -> None:
    path = [("16:40:00.000", 2000.75), ("16:41:00.000", 1998.75)]
    trades, _ = _run(path, replace(BASE, trade_direction="long"))
    # 2% of $10,000 over a $2.00 stop on 100 oz = 1.00 lot.
    assert trades.iloc[0].position_size == pytest.approx(1.0)
    capped, counts = _run(path, replace(BASE, trade_direction="long", leverage=5))
    # Margin at 1:5 allows 10,000 × 5 / (2001 × 100) = 0.24 lot.
    assert capped.iloc[0].position_size == pytest.approx(0.24) and counts["margin_reduced"] == 1


def test_position_carries_into_the_next_broker_day() -> None:
    first = _ticks([("16:40:00.000", 2000.75), ("16:41:00.000", 2001.50)])
    nxt = _ticks([("16:00:00.000", 1998.75)], day=date(2023, 3, 9))
    far = replace(BASE, trade_direction="long", trail_start_points=10_000)
    trades, _ = _run(None, far, days={DAY: first, date(2023, 3, 9): nxt})
    assert trades.iloc[0].exit_reason == "stop"
    assert trades.iloc[0].exit_time.tz_convert("America/New_York").date() == date(2023, 3, 9)


def test_holdout_is_never_read(tmp_path) -> None:
    config = replace(BASE, tick_cache_dir=str(tmp_path))
    loader = cached_tick_loader(config)
    assert loader(date(2023, 3, 8)) is None  # no file, but allowed
    with pytest.raises(ValueError, match="development_end"):
        loader(date(2023, 3, 11))
    # Past the 2025 boundary only with the dated opening recorded.
    with pytest.raises(ValueError, match="holdout_opened"):
        replace(BASE, development_end="2025-06-01", holdout_opened=None).validate()
    replace(BASE, development_end="2025-06-01", holdout_opened="2026-10-06").validate()
    with pytest.raises(ValueError):
        ScalperConfig(cutoff="09:20").validate()


def test_dashboard_lists_a_scalper_run_with_its_own_settings(tmp_path) -> None:
    import json

    from webapp.server import CONFIG, ResearchWeb
    from orb_research.report import metrics
    from orb_research.tick_scalper import serialise

    path = [("16:40:00.000", 2000.75), ("16:41:00.000", 2001.50), ("16:42:00.000", 2001.25)]
    trades, _ = _run(path, replace(BASE, trade_direction="long"))
    run = tmp_path / "outputs" / "runs" / "20230308T000000Z-abc123"
    run.mkdir(parents=True)
    trades.to_csv(run / "trades.csv", index=False)
    (run / "parameters.json").write_text(json.dumps(serialise(BASE), default=str), encoding="utf-8")
    (run / "metrics.json").write_text(json.dumps(metrics(trades, BASE)[0], default=str), encoding="utf-8")
    app = ResearchWeb(root=tmp_path, config_path=CONFIG)
    record = app.get_run(run.name)
    assert record["strategy"] == "tick_scalper"
    assert record["parameters"]["trail_points"] == 10 and "range_minutes" in record["parameters"]
    assert "stop_method" not in record["parameters"]
    report = app.report(run.name)
    assert report["trades"] == 1 and report["stats"]["initial_deposit"] == 10000
