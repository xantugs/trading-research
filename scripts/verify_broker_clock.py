"""Fail-closed FTMO server-clock verification, using only pre-2025 M1 bars."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from orb_research.config import Config, load_config
from orb_research.data_loader import decode_broker_clock, load_m1


# BLS releases scheduled for 08:30 Eastern, including US/EU DST mismatch weeks.
ANCHORS = (("2024-02-13", "CPI", "winter"), ("2024-06-12", "CPI", "summer"),
           ("2024-03-12", "CPI", "US summer / EU winter"),
           ("2024-11-01", "Employment Situation", "EU winter / US summer"),
           ("2023-03-14", "CPI", "US summer / EU winter"),
           ("2023-11-03", "Employment Situation", "EU winter / US summer"))
NY = ZoneInfo("America/New_York")


def session_instant(day: str, hhmm: str) -> pd.Timestamp:
    local = datetime.combine(datetime.fromisoformat(day).date(), time.fromisoformat(hhmm), NY)
    return pd.Timestamp(local).tz_convert("UTC")


def verify(raw: pd.DataFrame, config: Config) -> dict:
    """Require independent release and trading-week evidence for NY+7 decoding."""
    if config.development_end != "2025-01-01" or config.holdout_start != "2025-01-01":
        raise ValueError("verification requires the sealed 2025 holdout boundary")
    if raw.empty or raw.index.max() >= pd.Timestamp("2025-01-01", tz="UTC"):
        raise ValueError("verification received holdout or empty bars")
    corrected = replace(config, broker_clock="new_york_plus_7")
    decoded = decode_broker_clock(raw, corrected)
    if decoded.index.has_duplicates or not decoded.index.is_monotonic_increasing:
        raise ValueError("decoded clock is not unique and monotonic")
    checks = []
    for day, release, season in ANCHORS:
        window = raw.tick_volume.loc[day].between_time("15:30", "15:35")
        reference = raw.tick_volume.loc[day].between_time("14:30", "14:59")
        if window.empty or reference.empty:
            raise ValueError(f"missing release/reference bars on {day}")
        peak = window.idxmax()
        # Observe an actual activity burst, rather than only checking a tautological conversion.
        ratio = float(window.max() / max(1.0, reference.median()))
        interpreted = decode_broker_clock(raw.loc[[peak]], corrected).index[0]
        local = interpreted.tz_convert(NY)
        passed = ratio >= 2.0 and local.date().isoformat() == day and (8, 30) <= (local.hour, local.minute) <= (8, 35)
        checks.append({"date": day, "release": release, "season": season,
                       "stored_peak": peak.isoformat(), "real_utc": interpreted.isoformat(),
                       "new_york": local.isoformat(), "volume_to_reference_median": ratio,
                       "pass": passed})
    grouped = pd.Series(1, index=decoded.index).groupby(pd.Grouper(freq="W-SAT"))
    weeks = [part for _, part in grouped if len(part) > 500]
    if not weeks:
        raise ValueError("no complete trading weeks to verify")
    first = [part.index[0].tz_convert(NY) for part in weeks]
    last = [part.index[-1].tz_convert(NY) for part in weeks]
    open_share = sum(stamp.weekday() == 6 and 16 <= stamp.hour <= 19 for stamp in first) / len(weeks)
    close_share = sum(stamp.weekday() == 4 and 15 <= stamp.hour <= 19 for stamp in last) / len(weeks)
    session_examples = []
    for day in ("2024-02-13", "2024-06-12", "2024-03-12", "2024-11-01"):
        start, end = session_instant(day, "08:20"), session_instant(day, "16:00")
        session_examples.append({"date": day, "open_utc": start.isoformat(),
                                 "exit_utc": end.isoformat(),
                                 "open_ny": start.tz_convert(NY).isoformat(),
                                 "exit_ny": end.tz_convert(NY).isoformat(),
                                 "bars_present": start in decoded.index and end in decoded.index})
    passed = (all(item["pass"] for item in checks) and len(weeks) >= 300 and
              open_share >= 0.9 and close_share >= 0.9 and
              all(item["bars_present"] and item["open_ny"][11:16] == "08:20" and
                  item["exit_ny"][11:16] == "16:00" for item in session_examples))
    return {"pass": bool(passed), "mapping": "stored broker wall time = America/New_York + 7 hours",
            "release_checks": checks,
            "weekly_checks": {"weeks": len(weeks), "sunday_evening_open_share": open_share,
                              "late_friday_close_share": close_share,
                              "first_examples": [stamp.isoformat() for stamp in first[:4]],
                              "last_examples": [stamp.isoformat() for stamp in last[:4]]},
            "session_examples": session_examples, "holdout_touched": False,
            "source": "FTMO-Demo XAUUSD cached M1, 2018–2024 only"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/strategy_b_0820.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/strategy_b/clock_verification.json"))
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if config.broker_clock != "new_york_plus_7":
        raise ValueError("verification requires an explicit new_york_plus_7 configuration")
    raw, _ = load_m1(replace(config, broker_clock="stored_as_utc"))
    result = verify(raw, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Clock mapping: {'PASS' if result['pass'] else 'FAIL'} -> {args.output}")
    for item in result["release_checks"]:
        print(f"  {item['date']} {item['release']}: {item['stored_peak']} -> "
              f"{item['real_utc']} -> {item['new_york']} "
              f"(volume ratio {item['volume_to_reference_median']:.1f}; "
              f"{'PASS' if item['pass'] else 'FAIL'})")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
