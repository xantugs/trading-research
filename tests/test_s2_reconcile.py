"""The S2 reconciliation pairs EA deals into trades and matches them to engine checkpoints."""
from __future__ import annotations

from datetime import date

import pandas as pd

from scripts.s2_reconcile import match, read_log


def test_deals_pair_into_trades_and_match_the_engine_checkpoint(tmp_path) -> None:
    log = tmp_path / "S2.csv"
    log.write_text(
        "time,entry,type,lots,price,profit,commission,swap,position,balance,leverage\n"
        "2026.10.12 18:30:01,in,buy,4.00,25000.0,0.00,0.00,0.00,11,10000.00,8.00\n"
        "2026.10.12 22:59:02,out,sell,4.00,25100.0,400.00,0.00,0.00,11,10400.00,8.00\n", encoding="utf-8")
    live = read_log(log)
    assert len(live) == 1 and live.direction.iloc[0] == 1
    assert live.entry_time.iloc[0].strftime("%H:%M") == "11:30"   # server 18:30 = New York 11:30
    engine = pd.DataFrame([{"date": date(2026, 10, 12), "direction": 1, "entry_minute": 690,
                            "entry": 24999.0, "exit_minute": 960, "exit": 25098.0, "ret": 0.0039}])
    report = match(live, engine)
    assert report["matched"] == 1 and report["share_of_engine_matched"] == 1.0
