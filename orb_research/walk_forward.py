"""Fixed annual walk-forward selection for the 08:20 XAUUSD breakout buffer."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .backtester import simulate_signals
from .config import Config
from .strategy import Signal, find_signals


CANDIDATE_BUFFERS = (0.25, 0.35, 0.45, 0.55)
FOLDS = ((2018, 2021), (2019, 2022), (2020, 2023), (2021, 2024))


@dataclass(frozen=True)
class Fold:
    train_start: int
    test_year: int

    @property
    def train_end(self) -> int:
        return self.test_year


def fixed_folds() -> tuple[Fold, ...]:
    return tuple(Fold(*pair) for pair in FOLDS)


def validate_frozen_config(base: Config, *, expected_symbol: str = "XAUUSD") -> None:
    """Check every period and strategy choice before reading any market data.

    ``expected_symbol`` exists only so the frozen plan can be replayed unchanged
    on another instrument as a labelled replication. Every other structural
    choice stays fixed, and the default keeps the original XAUUSD guard.
    """
    base.validate()
    fixed = {
        "symbol": expected_symbol, "expected_server": "FTMO-Demo", "source": "mt5",
        "start": "2018-01-01", "development_end": "2025-01-01",
        "holdout_start": "2025-01-01", "session_timezone": "America/New_York",
        "session_open": "08:20", "session_exit": "16:00", "range_minutes": 60,
        "signal_minutes": 5, "risk_percent": 0.5, "reward_risk": 2.0,
        "stop_method": "opposite_range", "same_bar_rule": "sl_first",
    }
    mismatched = [name for name, value in fixed.items() if getattr(base, name) != value]
    if mismatched:
        raise ValueError(f"walk-forward structure is frozen; changed: {', '.join(mismatched)}")


def validate_walk_forward(base: Config, m1: pd.DataFrame, *,
                          expected_symbol: str = "XAUUSD") -> None:
    """Reject accidental changes to the prespecified structure and final holdout."""
    validate_frozen_config(base, expected_symbol=expected_symbol)
    if m1.empty or not isinstance(m1.index, pd.DatetimeIndex) or str(m1.index.tz) != "UTC":
        raise ValueError("walk-forward data must be nonempty UTC M1 bars")
    if m1.index.min() < pd.Timestamp(base.start, tz="UTC"):
        raise ValueError("M1 data begins before the configured development interval")
    if m1.index.max() >= pd.Timestamp(base.holdout_start, tz="UTC"):
        raise ValueError("final holdout data reached the walk-forward runner")


def filter_signals(signals: list[Signal], start_year: int, end_year: int) -> list[Signal]:
    left, right = f"{start_year}-01-01", f"{end_year}-01-01"
    return [signal for signal in signals if left <= signal.session_date < right]


def choose_buffer(training: pd.DataFrame) -> float:
    """Highest train mean net R; smaller buffer wins an exact tie."""
    usable = training.loc[training.trades > 0]
    if usable.empty:
        raise ValueError("no candidate produced training trades")
    ranked = usable.sort_values(["mean_r", "buffer"], ascending=[False, True], kind="stable")
    return float(ranked.iloc[0].buffer)


def performance(trades: pd.DataFrame, starting_equity: float) -> dict:
    """Closed-trade diagnostics, with the fold's actual starting balance."""
    if trades.empty:
        return {"trades": 0, "mean_r": None, "profit_factor": None,
                "win_rate": None, "return_pct": 0.0, "max_closed_dd_pct": 0.0,
                "end_equity": starting_equity, "time_exit_fraction": None}
    pnl = trades.PnL.to_numpy(dtype=float)
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    equity = np.r_[starting_equity, trades.equity_after.to_numpy(dtype=float)]
    peak = np.maximum.accumulate(equity)
    return {
        "trades": len(trades), "mean_r": float(trades.R_result.mean()),
        "profit_factor": float(wins / losses) if losses > 0 else None,
        "win_rate": float((pnl > 0).mean()),
        "return_pct": float((equity[-1] / starting_equity - 1) * 100),
        "max_closed_dd_pct": float(-((equity / peak - 1) * 100).min()),
        "end_equity": float(equity[-1]),
        "time_exit_fraction": float((trades.exit_reason == "time").mean()),
    }


def run_walk_forward(m1: pd.DataFrame, base: Config, *, emit=print,
                     expected_symbol: str = "XAUUSD") -> dict:
    """Select on each 3-year training window, then replay only the next year."""
    validate_walk_forward(base, m1, expected_symbol=expected_symbol)
    train_rows = []
    fold_rows = []
    oos_parts = []
    oos_equity = base.initial_equity

    for fold_number, fold in enumerate(fixed_folds(), start=1):
        train_end = pd.Timestamp(f"{fold.train_end}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{fold.test_year + 1}-01-01", tz="UTC")
        train_m1 = m1.loc[m1.index < train_end]
        test_prefix = m1.loc[m1.index < test_end]
        emit(f"Fold {fold_number}/4: training {fold.train_start}-{fold.train_end - 1}")
        this_training = []
        for buffer in CANDIDATE_BUFFERS:
            candidate = replace(base, breakout_buffer_atr=buffer)
            signals, scan = find_signals(train_m1, candidate)
            selected = filter_signals(signals, fold.train_start, fold.train_end)
            trades, execution = simulate_signals(train_m1, selected, candidate)
            result = performance(trades, base.initial_equity)
            row = {"fold": fold_number, "train_start": fold.train_start,
                   "train_end_inclusive": fold.train_end - 1, "test_year": fold.test_year,
                   "buffer": buffer, **result,
                   "signals": execution["signals"], "skipped": execution["skipped"],
                   "incomplete_range_sessions": scan["incomplete_range"]}
            this_training.append(row)
            train_rows.append(row)
            emit(f"  train buffer {buffer:.2f}: {result['trades']} trades, "
                 f"mean R {result['mean_r']:+.4f}" if result["mean_r"] is not None
                 else f"  train buffer {buffer:.2f}: no trades")

        chosen = choose_buffer(pd.DataFrame(this_training))
        chosen_row = next(row for row in this_training if row["buffer"] == chosen)
        emit(f"  selected {chosen:.2f} from training only; testing {fold.test_year}")
        candidate = replace(base, breakout_buffer_atr=chosen)
        signals, scan = find_signals(test_prefix, candidate)
        selected = filter_signals(signals, fold.test_year, fold.test_year + 1)
        starting_equity = oos_equity
        trades, execution = simulate_signals(test_prefix, selected, candidate,
                                             initial_equity=starting_equity)
        result = performance(trades, starting_equity)
        oos_equity = result["end_equity"]
        if not trades.empty:
            trades = trades.copy()
            trades["walk_forward_fold"] = fold_number
            trades["selected_buffer"] = chosen
            oos_parts.append(trades)
        fold_rows.append({
            "fold": fold_number, "train_start": fold.train_start,
            "train_end_inclusive": fold.train_end - 1, "test_year": fold.test_year,
            "selected_buffer": chosen, "selected_train_mean_r": chosen_row["mean_r"],
            "test_start_equity": starting_equity, **result,
            "test_signals": execution["signals"], "test_skipped": execution["skipped"],
            "incomplete_range_sessions": scan["incomplete_range"],
        })
        emit(f"  unseen {fold.test_year}: {result['trades']} trades, "
             f"mean R {result['mean_r']:+.4f}, return {result['return_pct']:+.2f}%"
             if result["mean_r"] is not None else f"  unseen {fold.test_year}: no trades")

    oos_trades = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
    combined = performance(oos_trades, base.initial_equity)
    return {
        "training": pd.DataFrame(train_rows), "folds": pd.DataFrame(fold_rows),
        "oos_trades": oos_trades, "combined": combined,
    }


def write_results(output: Path, results: dict, base: Config, provenance: dict,
                  replication_of: str | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    training = results["training"].copy()
    folds = results["folds"].copy()
    trades = results["oos_trades"].copy()
    training["skipped"] = training.skipped.map(json.dumps)
    folds["test_skipped"] = folds.test_skipped.map(json.dumps)
    training.to_csv(output / "training_selection.csv", index=False)
    folds.to_csv(output / "folds.csv", index=False)
    trades.to_csv(output / "oos_trades.csv", index=False)
    spec = {
        "purpose": "development-period walk-forward buffer selection",
        "candidate_buffers": list(CANDIDATE_BUFFERS),
        "folds": [asdict(fold) for fold in fixed_folds()],
        "selection": "highest train mean net R; smaller buffer breaks exact ties",
        "equity": "training resets to initial equity per candidate; unseen test years carry equity forward",
        "holdout_used": False, "holdout_start": base.holdout_start,
        "replication_of": replication_of,
        "base_config": asdict(base), "data_provenance": provenance,
        "code_sha256": {
            name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
            for name in ("strategy.py", "backtester.py", "walk_forward.py")
        },
    }
    (output / "spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    combined = results["combined"]
    (output / "summary.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    heading = (f"# {base.symbol} fixed walk-forward buffer test"
               + (f" — frozen {replication_of} plan replayed on {base.symbol}"
                  if replication_of else ""))
    lines = [
        heading, "",
        "Development data only. The 2025-onward final holdout was not read.", "",
        "Candidates: 0.25, 0.35, 0.45, 0.55 ATR; 08:20 New York open, 60-minute range, "
        "opposite-range stop, 2R target.", "",
        "| Train years | Unseen test | Chosen buffer | Train mean R | Test trades | Test mean R | "
        "Test return | Max closed DD |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in folds.itertuples(index=False):
        lines.append(f"| {row.train_start}–{row.train_end_inclusive} | {row.test_year} | "
                     f"{row.selected_buffer:.2f} | {row.selected_train_mean_r:+.3f} | "
                     f"{row.trades} | {row.mean_r:+.3f} | {row.return_pct:+.2f}% | "
                     f"{row.max_closed_dd_pct:.2f}% |")
    lines += ["", "## Concatenated unseen 2021–2024", "",
              f"Trades: {combined['trades']}; mean net R: {combined['mean_r']:+.4f}; "
              f"profit factor: {combined['profit_factor']:.3f}; "
              f"return: {combined['return_pct']:+.2f}%; "
              f"maximum closed-equity drawdown: {combined['max_closed_dd_pct']:.2f}%.", "",
              "Training uses only prior years. Each test year is evaluated once after its buffer is "
              "selected. This is still M1-bar execution with modeled costs and stop-first ambiguity; "
              "it is not a tick-exact prop-firm pass estimate. The candidate set was chosen after "
              "earlier inspection of 2018–2024, so even this walk-forward result is not fully "
              "independent evidence of the overall research process.", ""]
    report = output / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
