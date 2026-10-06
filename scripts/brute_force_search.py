#!/usr/bin/env python3
"""Run the brute-force ORB grid from docs/BRUTE_FORCE_SEARCH.md and apply its verdict.

Each symbol's M1 bars are loaded once per worker process; results stream to
results_<symbol>.csv so an interrupted run resumes where it stopped.
"""
from __future__ import annotations

# ruff: noqa: E402 -- imports follow the thread-limit block below on purpose
import os

# One numerical-library thread per worker: with a process per core, the libraries' own
# thread pools would otherwise oversubscribe the CPU many times over.  Set before
# NumPy loads; spawned workers re-import this module, so they inherit it too.
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse
import json
import math
import time
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product
from multiprocessing import Pool
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = {"US100.cash": "configs/indices/ftmo_us100_orb_framework_15_15.json",
           "US500.cash": "configs/indices/ftmo_us500_orb_framework_15_15.json",
           "XAUUSD": "configs/orb_framework.json"}
SPLIT = pd.Timestamp("2025-01-01", tz="America/New_York")
MIN_TRAIN, MIN_TEST = 200, 80
KEYS = ("range_minutes", "entry", "reward_risk", "stop", "cutoff", "direction", "exit", "atr_filter")

_M1 = _BASE = None


def grid() -> list[dict]:
    combos = []
    for rng, entry, rr, stop, cutoff, direction, exit_, atr in product(
            (5, 15, 30, 60), ("close_1", "close_5", "close_15", "stop_order"), (1.0, 1.5, 2.0, 3.0),
            ("far_side", "midpoint"), ("11:00", "13:00", "15:30"), ("both", "long"),
            ("stop_or_target", "flat_16"), ("off", "1.25-3")):
        combos.append(dict(zip(KEYS, (rng, entry, rr, stop, cutoff, direction, exit_, atr))))
    return combos


def overrides(combo: dict) -> dict:
    entry = combo["entry"]
    out = {"range_minutes": combo["range_minutes"], "reward_risk": combo["reward_risk"],
           "session_exit": combo["cutoff"], "trade_direction": combo["direction"],
           "trades_per_session": "one_per_direction",
           "entry_mode": "stop_order" if entry == "stop_order" else "close",
           "signal_minutes": 15 if entry == "stop_order" else int(entry.split("_")[1]),
           "stop_method": "range_multiple", "stop_width_multiple": 1.0 if combo["stop"] == "far_side" else 0.5,
           "position_exit": "stop_or_target" if combo["exit"] == "stop_or_target" else "end_of_day",
           "flat_time": None if combo["exit"] == "stop_or_target" else "16:00",
           "or_min_atr": None if combo["atr_filter"] == "off" else 1.25,
           "or_max_atr": None if combo["atr_filter"] == "off" else 3.0}
    return out


def _init(config_path: str) -> None:
    global _M1, _BASE
    from orb_research.config import load_config
    from orb_research.data_loader import load_m1
    base = load_config(ROOT / config_path)
    # Same window for every symbol; sizing large enough that the minimum lot never binds,
    # so R is unaffected by position size; FTMO-like overnight financing for all.
    _BASE = replace(base, start="2021-10-01", tick_start=max(base.tick_start, "2021-10-04"),
                    risk_percent=0.5, initial_equity=1_000_000.0, financing_pct_annual=8.0,
                    trail_atr_multiple=None, entry_cutoff_minutes_after_or=None, min_or_width_atr=None,
                    breakout_buffer_atr=0.0)
    _BASE.validate()
    _M1, _ = load_m1(_BASE)


def _stats(r: pd.Series, prefix: str) -> dict:
    n = len(r)
    sd = float(r.std(ddof=1)) if n > 1 else float("nan")
    mean = float(r.mean()) if n else float("nan")
    return {f"{prefix}_trades": n, f"{prefix}_mean_r": mean,
            f"{prefix}_t": mean / sd * math.sqrt(n) if n > 1 and sd > 0 else float("nan"),
            f"{prefix}_sharpe": mean / sd if n > 1 and sd > 0 else float("nan"),
            f"{prefix}_skew": float(r.skew()) if n > 2 else float("nan"),
            f"{prefix}_kurt": float(r.kurt()) + 3 if n > 3 else float("nan")}


def _run(combo: dict) -> dict:
    from orb_research.backtester import backtest
    config = replace(_BASE, **overrides(combo))
    config.validate()
    trades, _ = backtest(_M1, config)
    if trades.empty:
        return {**combo, "train_trades": 0, "test_trades": 0}
    stamp = pd.to_datetime(trades.timestamp, utc=True)
    train, test = trades[stamp < SPLIT], trades[stamp >= SPLIT]
    return {**combo, **_stats(train.R_result, "train"), **_stats(test.R_result, "test"),
            "full_mean_r": float(trades.R_result.mean())}


def search(symbol: str, out: Path, workers: int) -> pd.DataFrame:
    path = out / f"results_{symbol.split('.')[0]}.csv"
    done = pd.read_csv(path) if path.is_file() else pd.DataFrame()
    finished = {tuple(row[k] for k in KEYS) for _, row in done.iterrows()} if not done.empty else set()
    todo = [c for c in grid() if tuple(c[k] for k in KEYS) not in finished]
    print(f"{symbol}: {len(finished)} done, {len(todo)} to run", flush=True)
    started = time.monotonic()
    with Pool(workers, initializer=_init, initargs=(SYMBOLS[symbol],)) as pool:
        for i, row in enumerate(pool.imap_unordered(_run, todo, chunksize=4), 1):
            pd.DataFrame([row]).to_csv(path, mode="a", header=not path.is_file() or path.stat().st_size == 0,
                                       index=False)
            if i % 250 == 0 or i == len(todo):
                rate = i / (time.monotonic() - started)
                print(f"  {symbol} {i}/{len(todo)}  ({rate:.1f}/s, ~{(len(todo) - i) / rate / 60:.0f} min left)",
                      flush=True)
    return pd.read_csv(path)


def deflated_sharpe(best: pd.Series, trial_sharpes: np.ndarray, n_trials: int) -> float:
    """Bailey & López de Prado (2014): P(true Sharpe > 0) after selecting the best of n_trials."""
    gamma = 0.5772156649
    z = NormalDist().inv_cdf
    sr0 = math.sqrt(np.var(trial_sharpes, ddof=1)) * ((1 - gamma) * z(1 - 1 / n_trials)
                                                      + gamma * z(1 - 1 / (n_trials * math.e)))
    sr, t, skew, kurt = best.train_sharpe, best.train_trades, best.train_skew, best.train_kurt
    denominator = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return NormalDist().cdf((sr - sr0) * math.sqrt(t - 1) / denominator)


def verdict(results: dict[str, pd.DataFrame]) -> dict:
    eligible = {s: df[(df.train_trades >= MIN_TRAIN) & (df.test_trades >= MIN_TEST)].copy()
                for s, df in results.items()}
    n_trials = sum(len(df) for df in eligible.values())
    all_sharpes = np.concatenate([df.train_sharpe.dropna().to_numpy() for df in eligible.values()])
    report = {"eligible_trials": n_trials, "symbols": {}}
    for symbol, df in eligible.items():
        ranked = df.sort_values("train_t", ascending=False)
        top = ranked.head(10)
        best = ranked.iloc[0]
        rho = float(df.train_mean_r.rank().corr(df.test_mean_r.rank()))
        dsr = deflated_sharpe(best, all_sharpes, n_trials)
        conditions = {"a_best_test_positive_t2": bool(best.test_mean_r > 0 and best.test_t >= 2.0),
                      "b_top10_median_test_positive": bool(top.test_mean_r.median() > 0),
                      "c_deflated_sharpe_95": bool(dsr >= 0.95),
                      "d_spearman_above_0_1": bool(rho > 0.1)}
        report["symbols"][symbol] = {
            "eligible": len(df), "spearman_train_test": rho, "deflated_sharpe_best": dsr,
            "share_train_positive": float((df.train_mean_r > 0).mean()),
            "share_test_positive": float((df.test_mean_r > 0).mean()),
            "best": {k: (best[k].item() if hasattr(best[k], "item") else best[k]) for k in
                     (*KEYS, "train_trades", "train_mean_r", "train_t", "test_trades", "test_mean_r", "test_t")},
            "top10": top[[*KEYS, "train_trades", "train_mean_r", "train_t", "test_trades", "test_mean_r",
                          "test_t"]].to_dict(orient="records"),
            "conditions": conditions, "edge": all(conditions.values())}
    report["edge_found"] = any(s["edge"] for s in report["symbols"].values())
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workers", type=int, default=14)
    parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    args = parser.parse_args(argv)
    out = args.output_dir or ROOT / "outputs/search" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "protocol.json").write_text(json.dumps({"grid_size_per_symbol": len(grid()), "split": str(SPLIT.date()),
                                                   "min_train": MIN_TRAIN, "min_test": MIN_TEST,
                                                   "protocol": "docs/BRUTE_FORCE_SEARCH.md"}, indent=2), encoding="utf-8")
    results = {symbol: search(symbol, out, args.workers) for symbol in args.symbols}
    report = verdict(results)
    (out / "verdict.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for symbol, s in report["symbols"].items():
        print(f"{symbol}: eligible {s['eligible']}, spearman {s['spearman_train_test']:+.3f}, "
              f"DSR {s['deflated_sharpe_best']:.3f}, conditions {s['conditions']} -> edge {s['edge']}")
    print(f"Edge found: {report['edge_found']} | {out / 'verdict.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
