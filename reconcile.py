"""Compare the EA's trade log with the Python engine, trade by trade.

    python reconcile.py --ea-log "C:/Users/you/AppData/Roaming/MetaQuotes/Terminal/Common/Files/ORB_trades.csv" \
                        --bars data/NAS100_M15.csv --m1 data/NAS100_M1.csv --spread 1.0

The EA writes its settings in the first line of the log, so the Python run uses
the same parameters automatically (override any of them with --set key=value).
Trades are matched on (date, direction).  For every matched pair the script
compares entry time, entry price, stop, target, exit reason, exit price and R,
and lists the ones that differ by more than --tolerance price units.

Expect small entry-price differences from the spread model (the EA fills at the
real ask; here ask = bid + spread) and occasional exit differences where a bar
touches both stop and target.  Anything systematic - every entry one bar late,
wrong day, stops on the wrong side - is a bug in one of the two.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from orb import Params, infer_minutes, load_bars, resample, run_backtest
from orb.config import load_params

EA_TIME = "%Y.%m.%d %H:%M:%S"


def parse_ea_header(line: str) -> dict:
    """'# ORB EA v1.20 | symbol=NAS100 tf=PERIOD_M15 orb=16:30 cutoff=19 ...' -> dict."""
    fields = dict(re.findall(r"(\w+)=(\S+)", line))
    out: dict = {}
    if "tf" in fields:
        out["tf_minutes"] = tf_minutes(fields["tf"])        # not a Params field - popped by the caller
    if "orb" in fields:
        h, m = fields["orb"].split(":")
        out["orb_hour"], out["orb_minute"] = int(h), int(m)
    if "cutoff" in fields:
        out["cutoff_hour"] = int(fields["cutoff"])
    if "entry" in fields:
        out["entry_mode"] = "immediate" if fields["entry"].startswith("imm") else "close"
    if "direction" in fields:
        out["direction"] = fields["direction"]
    for key, name in (("tp", "tp_ratio"), ("sl", "sl_ratio"), ("risk", "risk_pct")):
        if key in fields:
            out[name] = float(fields[key])
    if "eod" in fields:
        out["eod_close"] = None if fields["eod"] == "off" else fields["eod"]
    if "range_atr" in fields:
        lo, hi = fields["range_atr"].split("-")
        out["min_range_atr"], out["max_range_atr"] = float(lo), float(hi)
    if "ma" in fields:
        period, tf, method = fields["ma"].split("/")
        out["filter_ma_period"] = int(period)
        out["filter_tf_minutes"] = tf_minutes(tf)
        if method not in ("MODE_SMA", "MODE_EMA"):
            raise ValueError(f"Python does not implement MA method {method}")
        out["filter_ma_method"] = "sma" if method == "MODE_SMA" else "ema"
    if "be" in fields:
        at, lock = fields["be"].split("/")
        out["breakeven_at_r"], out["breakeven_lock_r"] = float(at), float(lock)
    if "trail" in fields:
        start, dist, step = fields["trail"].split("/")
        out["trail_start_r"], out["trail_dist_r"], out["trail_step_r"] = float(start), float(dist), float(step)
    integer_fields = {
        "atr_period": "atr_period", "vol_lookback": "vol_lookback",
        "range_sessions": "range_history_sessions", "premarket_minutes": "premarket_window_minutes",
        "confirmation_bars": "confirmation_bars", "range_lookback_minutes": "range_lookback_minutes",
        "ma_slope_bars": "filter_ma_slope_bars", "min_stop_points": "min_stop_points",
    }
    for key, name in integer_fields.items():
        if key in fields:
            out[name] = int(fields[key])
    if "vol_spike_min" in fields:
        out["vol_spike_min"] = float(fields["vol_spike_min"])
    float_fields = {
        "range_percentile": "range_percentile", "range_multiplier": "range_multiplier",
        "premarket_sensitivity": "premarket_sensitivity",
        "max_range_lookback_ratio": "max_range_lookback_ratio",
        "ma_min_slope_pct": "filter_ma_min_slope_pct",
    }
    for key, name in float_fields.items():
        if key in fields:
            out[name] = float(fields[key])
    if "range_mode" in fields:
        out["range_mode"] = fields["range_mode"]
    if "stop_mode" in fields:
        out["stop_mode"] = fields["stop_mode"]
    if "confirmation_color" in fields:
        out["confirmation_same_color"] = fields["confirmation_color"].lower() == "true"
    for key in ("point", "point_value", "lot_step", "min_lot", "max_lot", "initial_balance"):
        if key in fields:
            out[key] = float(fields[key])
    if "max_spread_points" in fields:
        out["max_spread"] = float(fields["max_spread_points"]) * out.get("point", 1.0)
    return out


def tf_minutes(name: str) -> int:
    table = {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12, "M15": 15, "M20": 20,
             "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480, "H12": 720, "D1": 1440}
    name = name.replace("PERIOD_", "")
    if name not in table:
        raise ValueError(f"Unsupported EA timeframe: {name}")
    return table[name]


def load_ea_log(path: Path) -> tuple[pd.DataFrame, dict]:
    header = ""
    with path.open("r", encoding="utf-8-sig") as f:
        first = f.readline()
        if first.startswith("#"):
            header = first
    df = pd.read_csv(path, comment="#", encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d").dt.date
    for col in ("signal_time", "entry_time", "exit_time"):
        df[col] = pd.to_datetime(df[col], format=EA_TIME)
    df["orb_time"] = pd.to_datetime(df["orb_time"], format="%Y.%m.%d %H:%M")
    if df.duplicated(["date", "direction"]).any():
        raise ValueError("EA log has duplicate date/direction keys (partial exits or merged runs). Reconcile separately.")
    return df, parse_ea_header(header)


def estimate_point_value(ea: pd.DataFrame, risk_pct: float) -> float:
    """money per 1.0 price per lot, implied by the EA's own lot sizes."""
    before = ea["balance"] - ea["profit"] - ea["commission"] - ea["swap"]
    risk_money = before * risk_pct / 100.0
    risk_dist = (ea["entry"] - ea["sl"]).abs()
    est = risk_money / (ea["lots"] * risk_dist)
    return float(est.median())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ea-log", required=True)
    ap.add_argument("--bars", required=True, help="signal-timeframe bars CSV (e.g. M15)")
    ap.add_argument("--m1", default=None, help="M1 bars CSV for exit resolution (recommended)")
    ap.add_argument("--spread", type=float, default=0.0, help="constant spread in price units")
    ap.add_argument("--use-data-spread", action="store_true", help="use the spread column of the bars")
    ap.add_argument("--point", type=float, default=None)
    ap.add_argument("--tolerance", type=float, default=0.5, help="price difference that counts as a mismatch")
    ap.add_argument("--set", nargs="*", default=[], help="override params, e.g. --set tp_ratio=2 same_bar_rule=tp_first")
    ap.add_argument("--report", default="reconcile_report.csv")
    ap.add_argument("--strict", action="store_true", help="Exit 2 when any trade is missing or mismatched")
    ap.add_argument("--r-tolerance", type=float, default=0.01)
    args = ap.parse_args()

    ea, header_params = load_ea_log(Path(args.ea_log))
    tf_needed = header_params.pop("tf_minutes", None)
    params = Params(**header_params)
    params.spread = args.spread
    params.use_data_spread = args.use_data_spread
    if args.point is not None:
        params.point = args.point
    import json
    overrides = [f"{key}={json.dumps(value)}" for key, value in params.to_dict().items()]
    params = load_params(overrides=overrides + args.set)
    if args.tolerance < 0 or args.r_tolerance < 0:
        raise ValueError("Tolerances must be nonnegative")

    bars = load_bars(args.bars)
    m1 = load_bars(args.m1) if args.m1 else None
    have = infer_minutes(bars)
    if tf_needed and have < tf_needed:
        print(f"--bars is {have}-minute data; resampling to the EA's {tf_needed}-minute timeframe"
              + ("" if m1 is not None else " and using it as the exit path"))
        if m1 is None:
            m1 = bars
        bars = resample(bars, tf_needed)
    elif tf_needed and have > tf_needed:
        print(f"--bars is {have}-minute data but the EA ran on {tf_needed}-minute bars - export the right timeframe")
        return 1
    print(f"EA log: {len(ea)} trades   params from header: {header_params}")
    print(f"point_value used: {params.point_value:.4f}; money is not an execution-parity check")
    if "atr_period" not in header_params:
        print("Legacy header: ATR/volume/contract fields may be missing; supply --set overrides.")

    res = run_backtest(bars, params, path=m1)
    py = res.trades
    print(f"Python: {len(py)} trades")

    key = ["date", "direction"]
    merged = ea.merge(py, on=key, how="outer", suffixes=("_ea", "_py"), indicator=True, validate="one_to_one")
    only_ea = merged[merged["_merge"] == "left_only"]
    only_py = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"].copy()

    print(f"\nmatched {len(both)}   EA only {len(only_ea)}   Python only {len(only_py)}")
    if len(only_ea):
        print("\nEA-only trades (Python found no signal):")
        print(only_ea[key + ["entry_time_ea", "entry_ea", "reason_ea", "r_multiple"]].to_string(index=False))
    if len(only_py):
        print("\nPython-only trades (EA did not trade):")
        print(only_py[key + ["entry_time_py", "entry_py", "reason_py", "r"]].to_string(index=False))

    bad = both.iloc[:0]
    if len(both):
        both["d_entry"] = (both["entry_ea"] - both["entry_py"]).abs()
        both["d_sl"] = (both["sl_ea"] - both["sl_py"]).abs()
        both["d_tp"] = (both["tp_ea"] - both["tp_py"]).abs()
        both["d_exit"] = (both["exit_ea"] - both["exit_py"]).abs()
        both["d_r"] = (both["r_multiple"] - both["r"]).abs()
        both["entry_bar_same"] = both["entry_time_ea"].dt.floor("min") == both["entry_time_py"].dt.floor("min")
        both["reason_same"] = both["reason_ea"] == both["reason_py"]
        tol = args.tolerance
        bad = both[(~both["entry_bar_same"]) | (~both["reason_same"]) | (both["d_entry"] > tol) |
                   (both["d_sl"] > tol) | (both["d_tp"] > tol) | (both["d_exit"] > tol) |
                   (both["d_r"] > args.r_tolerance)]

        print("\nmatched pairs - differences:")
        print(f"  entry bar identical : {both['entry_bar_same'].mean():6.1%}")
        print(f"  exit reason identical: {both['reason_same'].mean():6.1%}")
        for col in ("d_entry", "d_sl", "d_tp", "d_exit", "d_r"):
            print(f"  {col:<8} median {both[col].median():8.4f}   max {both[col].max():8.4f}")
        print(f"  total R  EA {ea['r_multiple'].sum():8.2f}   Python {py['r'].sum():8.2f}")
        print(f"\n{len(bad)} pair(s) beyond tolerance {tol}")
        if len(bad):
            cols = key + ["entry_time_ea", "entry_time_py", "entry_ea", "entry_py", "reason_ea", "reason_py",
                          "exit_ea", "exit_py", "r_multiple", "r"]
            print(bad[cols].head(40).to_string(index=False))

    merged["mismatch"] = merged["_merge"] != "both"
    if len(bad):
        merged.loc[bad.index, "mismatch"] = True
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.report, index=False)
    print(f"\nfull comparison written to {args.report}")
    return 2 if args.strict and (len(only_ea) or len(only_py) or len(bad)) else 0


if __name__ == "__main__":
    sys.exit(main())
