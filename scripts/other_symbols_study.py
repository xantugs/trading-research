#!/usr/bin/env python3
"""Run the frozen US100 rules on other FTMO symbols (docs/OTHER_SYMBOLS_STUDY.md).

    python -m scripts.other_symbols_study configs    # one config per symbol, from the broker
    python -m scripts.other_symbols_study download   # cache M1 bars for every symbol
    python -m scripts.other_symbols_study run        # backtest, verdicts, cost stress
"""
# ruff: noqa: E402
from __future__ import annotations

import os

# One thread per worker; see scripts/brute_force_search.py.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import contextlib
import io
import json
import math
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

from orb_research.backtester import backtest
from orb_research.config import load_config
from orb_research.data_loader import load_m1

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "configs/indices/ftmo_us100_orb_framework_15_15.json"
CONFIG_DIR = ROOT / "configs/symbols"
OUT = ROOT / "outputs/symbols"
INDICES = ("US30.cash", "US500.cash", "US2000.cash")
END = "2026-10-07"
NY = "America/New_York"
FORWARD = pd.Timestamp("2025-01-01", tz=NY)
FORWARD_END = pd.Timestamp("2026-10-07", tz=NY)
LATEST_START = pd.Timestamp("2023-02-01", tz="UTC")


def _config_path(symbol: str) -> Path:
    return CONFIG_DIR / f"ftmo_{symbol.replace('.', '_').lower()}.json"


def write_configs() -> int:
    import MetaTrader5 as mt5  # type: ignore
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialization failed: {mt5.last_error()}")
    try:
        stocks = sorted(s.name for s in mt5.symbols_get() if s.path.startswith("Equities I CFD\\"))
        base = json.loads(CANDIDATE.read_text(encoding="utf-8"))
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        for symbol in (*stocks, *INDICES):
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            index = symbol in INDICES
            config = {**base, "symbol": symbol, "point": info.point,
                      "contract_size": info.trade_contract_size, "lot_step": info.volume_step,
                      "min_lot": info.volume_min, "max_lot": info.volume_max,
                      "cache_dir": "C:/data/ftmo_indices_m1" if index else "C:/data/ftmo_stocks_m1",
                      "tick_cache_dir": "C:/data/ftmo_indices_ticks" if index else "C:/data/ftmo_stocks_ticks",
                      "output_dir": f"outputs/symbols/{symbol}", "initial_equity": 100000,
                      "risk_percent": 0.5,
                      # set per symbol by `run` from the cached spreads (see the study doc)
                      "fallback_spread_price": info.point, "slippage_price": info.point}
            _config_path(symbol).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"{len(stocks)} stocks + {len(INDICES)} indices -> {CONFIG_DIR}")
    finally:
        mt5.shutdown()
    return 0


def download() -> int:
    from scripts import download_mt5_m1
    log = {}
    for path in sorted(CONFIG_DIR.glob("ftmo_*.json")):
        config = load_config(path)
        cache = Path(config.cache_dir) / config.expected_server / config.symbol
        accepted: list[str] = []
        while True:
            argv = ["--config", str(path), "--end", END] + [x for m in accepted for x in ("--accept-gap", m)]
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = download_mt5_m1.main(argv)
            if code == 0:
                log[config.symbol] = {"status": "ok", "accepted_gaps": accepted}
                break
            # Months are written in order, so the failing one is the first without a sidecar.
            months = pd.period_range(config.start, pd.Timestamp(END) - pd.Timedelta(days=1), freq="M")
            missing = [f"{m:%Y-%m}" for m in months if not (cache / f"{m:%Y%m}.json").exists()]
            if not missing or missing[0] in accepted:
                log[config.symbol] = {"status": "failed", "error": err.getvalue().strip()}
                break
            accepted.append(missing[0])
        print(f"{config.symbol}: {log[config.symbol]['status']} "
              f"({len(accepted)} accepted gap months)", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "download_log.json").write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    return 0


def _stats(r: pd.Series) -> dict:
    n = len(r)
    sd = float(r.std(ddof=1)) if n > 1 else float("nan")
    return {"trades": n, "mean_r": float(r.mean()) if n else None,
            "t": float(r.mean() / sd * math.sqrt(n)) if n > 1 and sd > 0 else None}


def _one(path: str) -> dict:
    config = load_config(path)
    m1, _ = load_m1(config)
    first = m1.index.min()
    row = {"symbol": config.symbol, "first_bar": first.isoformat()}
    if first >= LATEST_START:
        return {**row, "excluded": "history starts after 2023-01"}
    local = m1.index.tz_convert(NY)
    minutes = local.hour * 60 + local.minute
    window = (minutes >= 570) & (minutes < 720) & (local < FORWARD) & (m1.spread.to_numpy() > 0)
    median_spread = float(m1.spread[window].median()) * config.point
    if not math.isfinite(median_spread):
        return {**row, "excluded": "no recorded spread"}
    costs = {"fallback_spread_price": median_spread,
             "slippage_price": max(config.point, 0.1 * median_spread)}
    config = replace(config, **costs)
    trades = backtest(m1, config)[0]
    stamp = pd.to_datetime(trades.timestamp, utc=True)
    back = _stats(trades.R_result[stamp < FORWARD])
    fwd = _stats(trades.R_result[(stamp >= FORWARD) & (stamp < FORWARD_END)])
    row.update({**costs, "backtest": back, "forward": fwd})
    if back["trades"] < 100:
        return {**row, "excluded": "fewer than 100 backtest trades"}
    passed = (back["mean_r"] > 0 and back["t"] is not None and back["t"] >= 2.0
              and fwd["mean_r"] is not None and fwd["mean_r"] > 0)
    row["passed"] = bool(passed)
    if passed:
        stressed = backtest(m1, replace(config, spread_multiplier=2.0,
                                        slippage_price=2 * costs["slippage_price"]))[0]
        s_stamp = pd.to_datetime(stressed.timestamp, utc=True)
        row["stress"] = {"backtest": _stats(stressed.R_result[s_stamp < FORWARD]),
                         "forward": _stats(stressed.R_result[(s_stamp >= FORWARD) & (s_stamp < FORWARD_END)])}
    keep = trades[(stamp < FORWARD_END)][["timestamp", "direction", "R_result"]].assign(symbol=config.symbol)
    OUT.mkdir(parents=True, exist_ok=True)
    keep.to_csv(OUT / f"trades_{config.symbol}.csv", index=False)
    return row


def run(workers: int) -> int:
    paths = [str(p) for p in sorted(CONFIG_DIR.glob("ftmo_*.json"))]
    with Pool(workers) as pool:
        rows = []
        for row in pool.imap_unordered(_one, paths):
            rows.append(row)
            if "excluded" in row:
                print(f"{row['symbol']:12s} excluded: {row['excluded']}", flush=True)
            else:
                b, f = row["backtest"], row["forward"]
                print(f"{row['symbol']:12s} backtest {b['mean_r']:+.3f}R t={b['t']:+.2f} ({b['trades']})  "
                      f"forward {f['mean_r']:+.3f}R ({f['trades']})  passed: {row['passed']}", flush=True)
    rows.sort(key=lambda r: r["symbol"])
    tested = [r for r in rows if "excluded" not in r]
    pooled = pd.concat([pd.read_csv(OUT / f"trades_{r['symbol']}.csv") for r in tested])
    stamp = pd.to_datetime(pooled.timestamp, utc=True)
    summary = {"tested": len(tested), "excluded": len(rows) - len(tested),
               "passed": [r["symbol"] for r in tested if r["passed"]],
               "chance_expectation": round(0.0115 * len(tested), 2),
               "pooled_backtest": _stats(pooled.R_result[stamp < FORWARD]),
               "pooled_forward": _stats(pooled.R_result[stamp >= FORWARD]),
               "positive_backtest": sum(r["backtest"]["mean_r"] > 0 for r in tested),
               "positive_forward": sum((r["forward"]["mean_r"] or 0) > 0 for r in tested)}
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "symbols": rows}, indent=2) + "\n",
                                      encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("configs", "download", "run"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if args.step == "configs":
        return write_configs()
    if args.step == "download":
        return download()
    return run(args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
