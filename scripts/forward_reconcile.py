#!/usr/bin/env python3
"""Reconcile the live ORB_Framework EA trade log against the Python engine.

Reads one or more EA logs (MT5 Common\\Files\\ORB_<login>_<symbol>.csv), downloads the
same period's M1 bars from the connected FTMO terminal (read-only), replays the frozen
candidate on those days and compares the two trade by trade.  It then applies the
pre-declared rules of docs/US100_FORWARD_TEST.md: fidelity, the 60-trade checkpoint, the
150-trade read and the drawdown stop.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs/indices/ftmo_us100_orb_framework_15_15.json"
BROKER_OFFSET = pd.Timedelta(hours=7)  # FTMO server clock = New York + 7 h
BACKTEST = {"mean_r": 0.0812, "sd_r": 1.243, "max_dd_pct": {0.5: 12.2, 1.5: 33.3}}
REASONS = {"SL": "stop", "TP": "target", "EOD": "time", "OTHER": "other", "STOPOUT": "stopout"}


def broker_to_utc(text: pd.Series) -> pd.Series:
    """EA log times are broker wall clock ('2026.10.07 16:45:00'); decode to UTC."""
    naive = pd.to_datetime(text, format="%Y.%m.%d %H:%M:%S") - BROKER_OFFSET
    return naive.dt.tz_localize("America/New_York").dt.tz_convert("UTC")


def read_log(path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Parse an EA log; '#' lines are the settings written at each start."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    settings = [line for line in lines if line.startswith("#")]
    rows = [line for line in lines if line and not line.startswith("#")]
    if not rows:
        return pd.DataFrame(), settings
    from io import StringIO
    frame = pd.read_csv(StringIO("\n".join(rows)))
    for column in ("signal_time", "entry_time", "exit_time"):
        frame[column] = broker_to_utc(frame[column])
    frame["session_date"] = pd.to_datetime(frame["date"], format="%Y.%m.%d").dt.date.astype(str)
    frame["exit_reason"] = frame.reason.map(REASONS).fillna("other")
    return frame, settings


def net_r(live: pd.DataFrame, contract_size: float) -> pd.Series:
    """Net P/L over the money at risk at entry (entry-to-stop distance × lots × contract)."""
    risk_money = (live.entry - live.sl).abs() * live.lots * contract_size
    return (live.profit + live.commission + live.swap) / risk_money


def match(live: pd.DataFrame, engine: pd.DataFrame) -> pd.DataFrame:
    """Pair live and engine trades by session date and direction (one per direction per day)."""
    keys = ["session_date", "direction"]
    left = live.rename(columns={c: f"live_{c}" for c in live.columns if c not in keys})
    right = engine.rename(columns={c: f"engine_{c}" for c in engine.columns if c not in keys})
    merged = left.merge(right, on=keys, how="outer", indicator=True)
    merged["status"] = merged["_merge"].map({"both": "matched", "left_only": "live_only",
                                             "right_only": "engine_only"})
    return merged.drop(columns="_merge").sort_values(keys).reset_index(drop=True)


def evaluate(merged: pd.DataFrame, risk_pct: float) -> dict:
    """Apply the fidelity and performance rules of the forward-test plan."""
    matched = merged[merged.status == "matched"]
    engine_days = (merged.status != "live_only").sum()
    live_days = (merged.status != "engine_only").sum()
    entry_gap = (matched.live_entry - matched.engine_entry).abs()
    fidelity = {
        "engine_signals_traded_live": float(len(matched) / engine_days) if engine_days else None,
        "live_trades_found_by_engine": float(len(matched) / live_days) if live_days else None,
        "median_entry_difference_points": float(entry_gap.median()) if len(matched) else None,
        "exit_reason_agreement": float((matched.live_exit_reason == matched.engine_exit_reason).mean())
        if len(matched) else None,
    }
    fidelity["pass"] = bool(len(matched) and fidelity["engine_signals_traded_live"] >= 0.9
                            and fidelity["live_trades_found_by_engine"] >= 0.9
                            and fidelity["median_entry_difference_points"] <= 1.5
                            and fidelity["exit_reason_agreement"] >= 0.9)
    live = merged[merged.status != "engine_only"].sort_values("live_exit_time")
    r = live.live_net_r.dropna().to_numpy()
    n = len(r)
    equity = np.cumprod(1 + risk_pct / 100 * r) if n else np.array([1.0])
    drawdown = float((1 - equity / np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]).max() * 100) if n else 0.0
    performance = {"live_trades": n, "live_mean_r": float(r.mean()) if n else None,
                   "live_drawdown_pct": drawdown, "drawdown_stop_pct": BACKTEST["max_dd_pct"].get(risk_pct)}
    stop = performance["drawdown_stop_pct"]
    if stop is not None and drawdown > stop:
        verdict = f"STOP: drawdown {drawdown:.1f}% is beyond the backtest's worst {stop}%"
    elif n >= 150:
        mean = float(r[:150].mean())
        verdict = ("SUPPORTED" if mean > 0 else "INCONCLUSIVE" if mean >= -0.12 else "NOT SUPPORTED") + \
                  f" at 150 trades (mean {mean:+.3f}R)"
    elif n >= 60 and float(r[:60].mean()) < -0.24:
        verdict = f"STOP at the 60-trade checkpoint (mean {float(r[:60].mean()):+.3f}R < -0.24R)"
    elif n >= 60:
        verdict = f"CONTINUE: passed the 60-trade checkpoint; {150 - n} trades to the final read"
    else:
        verdict = f"CONTINUE: {n} live trades; checkpoint at 60"
    performance["verdict"] = verdict
    return {"fidelity": fidelity, "performance": performance}


def engine_trades(first: date, last: date, config_path: Path, terminal_path: str | None) -> pd.DataFrame:
    """Download the period's M1 bars (read-only) and replay the frozen candidate on them."""
    import MetaTrader5 as mt5  # type: ignore

    from orb_research.backtester import backtest
    from orb_research.config import load_config
    from orb_research.data_loader import _from_mt5_array, _inspect_terminal, decode_broker_clock, validate_m1_bars

    base = load_config(config_path)
    # Room for positions held past the last entry, but never beyond today.
    end = min(last + timedelta(days=12), date.today() + timedelta(days=1))
    # The forward period is the data under test here, so the replay's window boundary is
    # its own end; the research holdout boundary does not apply to new live data.
    config = replace(base, start=first.isoformat(), tick_start=first.isoformat(),
                     development_end=end.isoformat(), holdout_start=end.isoformat(), holdout_opened=None)
    config.validate()
    if not (mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()):
        raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        _inspect_terminal(mt5, config)
        a = datetime.combine(first - timedelta(days=1), datetime.min.time(), timezone.utc)
        b = datetime.combine(date.fromisoformat(config.development_end), datetime.min.time(), timezone.utc)
        rates = mt5.copy_rates_range(config.symbol, mt5.TIMEFRAME_M1, a, b)
    finally:
        mt5.shutdown()
    frame = validate_m1_bars(_from_mt5_array(rates))
    trades, _ = backtest(decode_broker_clock(frame, config), config)
    return trades[(trades.session_date >= first.isoformat()) & (trades.session_date <= last.isoformat())]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, nargs="+", required=True, help="EA trade log(s)")
    parser.add_argument("--config", type=Path, default=CANDIDATE)
    parser.add_argument("--terminal-path", help="terminal64.exe when several MT5 terminals are installed")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    out = args.output_dir or ROOT / "outputs/forward" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    from orb_research.config import load_config
    contract = load_config(args.config).contract_size
    summary = {}
    for log in args.log:
        live, settings = read_log(log)
        if live.empty:
            print(f"{log.name}: no closed trades yet")
            continue
        risk = float(settings[-1].split("risk=")[1].split()[0]) if settings else 0.5
        live["net_r"] = net_r(live, contract)
        first, last = min(live.session_date), max(live.session_date)
        engine = engine_trades(date.fromisoformat(first), date.fromisoformat(last), args.config, args.terminal_path)
        engine = engine.assign(timestamp=pd.to_datetime(engine.timestamp, utc=True))[
            ["session_date", "direction", "timestamp", "entry", "stop", "exit_time", "exit", "exit_reason", "R_result"]]
        engine["exit_reason"] = engine.exit_reason.str.replace("_gap", "", regex=False)
        merged = match(live[["session_date", "direction", "entry_time", "entry", "sl", "exit_time", "exit",
                             "exit_reason", "lots", "net_r"]], engine)
        result = evaluate(merged, risk)
        merged.to_csv(out / f"{log.stem}_matched.csv", index=False)
        summary[log.name] = {"risk_percent": risk, "window": [first, last], "settings": settings[-1] if settings else None,
                             **result, "counts": merged.status.value_counts().to_dict()}
        f, p = result["fidelity"], result["performance"]
        print(f"{log.name} ({risk}% risk, {first} to {last}): {merged.status.value_counts().to_dict()}")
        print(f"   fidelity {'PASS' if f['pass'] else 'FAIL'}: {json.dumps({k: v for k, v in f.items() if k != 'pass'})}")
        print(f"   {p['verdict']} | live mean R {p['live_mean_r']} | drawdown {p['live_drawdown_pct']:.1f}%")
    (out / "reconciliation.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(out / "reconciliation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
