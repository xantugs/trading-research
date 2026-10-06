"""Prespecified 4×4, train-only OR-length/buffer walk-forward for Strategy B."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .backtester import minimum_lot_feasibility, simulate_signals
from .config import Config
from .strategy import find_signals
from .walk_forward import (CANDIDATE_BUFFERS, filter_signals, fixed_folds, performance,
                           validate_walk_forward)


RANGE_LENGTHS = (15, 30, 45, 60)
BOOTSTRAP_DRAWS = 4000
BOOTSTRAP_SEED = 20260918


def _fmt(value, pattern: str) -> str:
    return format(float(value), pattern) if value is not None and pd.notna(value) else "—"


def choose_cell(surface: pd.DataFrame, min_trades: int) -> tuple[int, float]:
    """Highest eligible train mean R; ties choose shorter OR, then lower buffer."""
    eligible = surface.loc[(surface.trades >= min_trades) & surface.mean_r.notna()]
    if eligible.empty:
        raise ValueError("no 4×4 cell has enough training trades for inference")
    ranked = eligible.sort_values(["mean_r", "range_minutes", "buffer"],
                                  ascending=[False, True, True], kind="stable")
    winner = ranked.iloc[0]
    return int(winner.range_minutes), float(winner.buffer)


def _direction_stats(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"median_r": None, "long_mean_r": None, "short_mean_r": None,
                "long_trades": 0, "short_trades": 0}
    longs = trades.loc[trades.direction == "long"].R_result
    shorts = trades.loc[trades.direction == "short"].R_result
    return {"median_r": float(trades.R_result.median()),
            "long_mean_r": float(longs.mean()) if len(longs) else None,
            "short_mean_r": float(shorts.mean()) if len(shorts) else None,
            "long_trades": len(longs), "short_trades": len(shorts)}


def _block_bootstrap(trades: pd.DataFrame) -> dict:
    """Resample session dates, keeping every trade from a picked session together."""
    if trades.empty:
        raise ValueError("no selected-path OOS trades to bootstrap")
    groups = [part.R_result.to_numpy(dtype=float) for _, part in trades.groupby("session_date")]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    n = len(groups)
    draws = np.empty(BOOTSTRAP_DRAWS)
    for i in range(BOOTSTRAP_DRAWS):
        picked = rng.integers(0, n, n)
        sample = np.concatenate([groups[k] for k in picked])
        draws[i] = sample.mean()
    return {"unit": "session_date", "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            "mean_r": float(trades.R_result.mean()),
            "ci_low": float(np.percentile(draws, 2.5)),
            "ci_high": float(np.percentile(draws, 97.5)),
            "p_mean_le_zero": float((draws <= 0).mean())}


def _top3_in_one_block(top: pd.DataFrame) -> bool:
    """A plateau means the top three eligible cells fit in one adjacent 2×2 block."""
    if len(top) < 3:
        return False
    or_indices = [RANGE_LENGTHS.index(int(v)) for v in top.range_minutes]
    buffer_indices = [CANDIDATE_BUFFERS.index(float(v)) for v in top.buffer]
    return max(or_indices) - min(or_indices) <= 1 and max(buffer_indices) - min(buffer_indices) <= 1


def stability(surface: pd.DataFrame, path: pd.DataFrame) -> dict:
    cells = pd.MultiIndex.from_product((RANGE_LENGTHS, CANDIDATE_BUFFERS),
                                       names=["range_minutes", "buffer"])
    ranks, tops, positive, plateaus = {}, {}, {}, {}
    for fold in range(1, 5):
        frame = surface.loc[surface.fold == fold].set_index(["range_minutes", "buffer"]).reindex(cells)
        ranks[fold] = frame.mean_r.rank(ascending=False)
        eligible = frame.loc[frame.eligible].reset_index().sort_values(
            ["mean_r", "range_minutes", "buffer"], ascending=[False, True, True])
        top = eligible.head(3)
        tops[fold] = set(zip(top.range_minutes.astype(int), top.buffer.astype(float)))
        positive[fold] = int((frame.mean_r > 0).sum())
        plateaus[fold] = _top3_in_one_block(top)
    # Spearman = Pearson correlation of within-fold ranks; avoids a SciPy dependency.
    correlations = {f"{i}->{i+1}": float(ranks[i].corr(ranks[i+1], method="pearson"))
                    for i in range(1, 4)}
    overlaps = {f"{i}->{i+1}": len(tops[i] & tops[i+1]) for i in range(1, 4)}
    selected = path[["selected_or_minutes", "selected_buffer"]]
    or_span = max(RANGE_LENGTHS.index(int(x)) for x in selected.selected_or_minutes) - min(
        RANGE_LENGTHS.index(int(x)) for x in selected.selected_or_minutes)
    buffer_span = max(CANDIDATE_BUFFERS.index(float(x)) for x in selected.selected_buffer) - min(
        CANDIDATE_BUFFERS.index(float(x)) for x in selected.selected_buffer)
    return {"spearman_consecutive": correlations, "top3_overlap": overlaps,
            "positive_cells_by_fold": positive, "top3_adjacent_2x2_by_fold": plateaus,
            "all_top3_are_plateaus": all(plateaus.values()),
            "selected_path_adjacent_2x2": or_span <= 1 and buffer_span <= 1}


def run_sweep(m1: pd.DataFrame, base: Config, *, emit=print) -> dict:
    """Evaluate 16 training cells per fold, then only the selected test cell."""
    validate_walk_forward(base, m1)
    if base.broker_clock != "new_york_plus_7" or base.initial_equity != 100_000:
        raise ValueError("the Strategy B sweep requires corrected clock and $100k research sizing")
    if base.entry_cutoff_minutes_after_or is not None or base.min_or_width_atr is not None:
        raise ValueError("Strategy B sweep must not carry the retired filters")
    surface_rows, path_rows, oos_parts = [], [], []
    equity = base.initial_equity
    for number, fold in enumerate(fixed_folds(), start=1):
        train_end = pd.Timestamp(f"{fold.train_end}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{fold.test_year + 1}-01-01", tz="UTC")
        training = m1.loc[m1.index < train_end]
        rows = []
        emit(f"Fold {number}/4 train {fold.train_start}-{fold.train_end-1}: 16 cells")
        for length in RANGE_LENGTHS:
            for buffer in CANDIDATE_BUFFERS:
                candidate = replace(base, range_minutes=length, breakout_buffer_atr=buffer)
                signals, _ = find_signals(training, candidate)
                selected = filter_signals(signals, fold.train_start, fold.train_end)
                trades, execution = simulate_signals(training, selected, candidate)
                feasible = minimum_lot_feasibility(training, selected, candidate)
                metrics = performance(trades, base.initial_equity)
                row = {"fold": number, "train_start": fold.train_start,
                       "train_end_inclusive": fold.train_end - 1, "test_year": fold.test_year,
                       "range_minutes": length, "buffer": buffer,
                       "trades": metrics["trades"], "mean_r": metrics["mean_r"],
                       "profit_factor": metrics["profit_factor"], "win_rate": metrics["win_rate"],
                       "time_exit_fraction": metrics["time_exit_fraction"],
                       "signals": execution["signals"],
                       "below_min_lot_skips": execution["skipped"].get("below_min_lot", 0),
                       "infeasible_5k_signals": feasible["infeasible_at_5k"],
                       "valid_sizing_signals": feasible["valid_sizing_signals"],
                       "infeasible_5k_share": feasible["infeasible_share"],
                       **_direction_stats(trades)}
                row["eligible"] = row["trades"] >= base.min_trades_for_inference
                rows.append(row)
            emit(f"  OR {length}m complete",)
        censored = [(row["range_minutes"], row["buffer"], row["below_min_lot_skips"])
                    for row in rows if row["below_min_lot_skips"]]
        if censored:
            raise ValueError(f"research sizing still censors fold {number} training cells: {censored}")
        chosen_or, chosen_buffer = choose_cell(pd.DataFrame(rows), base.min_trades_for_inference)
        for row in rows:
            row["selected"] = row["range_minutes"] == chosen_or and row["buffer"] == chosen_buffer
        surface_rows.extend(rows)
        chosen_train = next(row for row in rows if row["selected"])
        candidate = replace(base, range_minutes=chosen_or, breakout_buffer_atr=chosen_buffer)
        prefix = m1.loc[m1.index < test_end]
        signals, _ = find_signals(prefix, candidate)
        selected = filter_signals(signals, fold.test_year, fold.test_year + 1)
        start_equity = equity
        trades, execution = simulate_signals(prefix, selected, candidate, initial_equity=equity)
        if execution["skipped"].get("below_min_lot", 0):
            raise ValueError(f"research sizing still censors unseen {fold.test_year}: "
                             f"{execution['skipped']['below_min_lot']} below-min-lot signals")
        metrics = performance(trades, start_equity)
        equity = metrics["end_equity"]
        feasible = minimum_lot_feasibility(prefix, selected, candidate)
        if not trades.empty:
            trades = trades.copy()
            trades["walk_forward_fold"] = number
            trades["selected_or_minutes"] = chosen_or
            trades["selected_buffer"] = chosen_buffer
            oos_parts.append(trades)
        path_rows.append({"fold": number, "train_start": fold.train_start,
                          "train_end_inclusive": fold.train_end - 1, "test_year": fold.test_year,
                          "selected_or_minutes": chosen_or, "selected_buffer": chosen_buffer,
                          "training_trades": chosen_train["trades"],
                          "training_mean_r": chosen_train["mean_r"],
                          "test_start_equity": start_equity,
                          "test_trades": metrics["trades"], "test_mean_r": metrics["mean_r"],
                          "test_profit_factor": metrics["profit_factor"],
                          "test_return_pct": metrics["return_pct"],
                          "test_max_closed_dd_pct": metrics["max_closed_dd_pct"],
                          "test_below_min_lot_skips": execution["skipped"].get("below_min_lot", 0),
                          "test_5k_infeasible_signals": feasible["infeasible_at_5k"],
                          "test_5k_valid_sizing_signals": feasible["valid_sizing_signals"],
                          **{f"test_{key}": value for key, value in _direction_stats(trades).items()}})
        emit(f"  selected OR {chosen_or}m / buffer {chosen_buffer:.2f} from training only; "
             f"unseen {fold.test_year}: {metrics['trades']} trades, "
             f"mean R {metrics['mean_r']:+.4f}" if metrics["mean_r"] is not None else
             f"  selected OR {chosen_or}m / buffer {chosen_buffer:.2f}: no test trades")
    surface = pd.DataFrame(surface_rows)
    path = pd.DataFrame(path_rows)
    oos = pd.concat(oos_parts, ignore_index=True) if oos_parts else pd.DataFrame()
    combined = performance(oos, base.initial_equity)
    bootstrap = _block_bootstrap(oos)
    stable = stability(surface, path)
    years_positive = int((path.test_return_pct > 0).sum())
    supported = (combined["mean_r"] is not None and combined["mean_r"] > 0 and
                 bootstrap["p_mean_le_zero"] < 0.10 and years_positive >= 3 and
                 stable["all_top3_are_plateaus"])
    return {"training_surface": surface, "selected_path": path, "oos_trades": oos,
            "combined": combined, "bootstrap": bootstrap, "stability": stable,
            "years_positive": years_positive, "supported": bool(supported)}


def _extra_summary(trades: pd.DataFrame, path: pd.DataFrame, starting_equity: float) -> dict:
    ordered = trades.sort_values("exit_time").reset_index(drop=True)
    equity = np.r_[starting_equity, ordered.equity_after.to_numpy(dtype=float)]
    times = [pd.Timestamp("2021-01-01", tz="UTC")] + list(pd.to_datetime(ordered.exit_time, utc=True))
    high, high_time, duration = equity[0], times[0], 0.0
    for balance, stamp in zip(equity[1:], times[1:]):
        if balance >= high:
            high, high_time = balance, stamp
        else:
            duration = max(duration, (stamp - high_time).total_seconds() / 86400)
    losing = streak = 0
    for value in ordered.R_result:
        losing = losing + 1 if value < 0 else 0
        streak = max(streak, losing)
    annual = {}
    for row in path.itertuples(index=False):
        part = ordered.loc[ordered.session_date.str.startswith(str(row.test_year))]
        annual[str(row.test_year)] = {"return_pct": row.test_return_pct,
                                      **_direction_stats(part)}
    return {"median_r": float(ordered.R_result.median()),
            "longest_losing_streak": streak, "max_closed_drawdown_duration_days": duration,
            "positive_years": int((path.test_return_pct > 0).sum()),
            "yearly": annual, **_direction_stats(ordered)}


def write_results(output: Path, result: dict, base: Config, provenance: dict) -> Path:
    """Write only training surfaces and selected-path OOS; never all-cell OOS."""
    output.mkdir(parents=True, exist_ok=False)
    surface, path, oos = result["training_surface"], result["selected_path"], result["oos_trades"]
    if (oos.session_date >= "2025-01-01").any():
        raise ValueError("final holdout reached selected-path outputs")
    surface.to_csv(output / "training_surface.csv", index=False)
    path.to_csv(output / "selected_path.csv", index=False)
    oos.to_csv(output / "oos_trades.csv", index=False)
    extra = _extra_summary(oos, path, base.initial_equity)
    summary = {**result["combined"], **extra, "bootstrap": result["bootstrap"],
               "stability": result["stability"], "supported": result["supported"],
               "holdout_used": False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    spec = {"purpose": "Strategy B prespecified 4x4 OR-length/buffer walk-forward",
            "range_minutes": list(RANGE_LENGTHS), "buffers": list(CANDIDATE_BUFFERS),
            "folds": [asdict(fold) for fold in fixed_folds()],
            "selection": "highest training mean net R among eligible; ties shorter OR then lower buffer",
            "eligibility": f"training trades >= {base.min_trades_for_inference}",
            "research_sizing": {"initial_equity": base.initial_equity,
                                "risk_percent": base.risk_percent,
                                "lot_step": base.lot_step, "min_lot": base.min_lot},
            "support_rule": "OOS mean R > 0; session-bootstrap P(mean<=0)<0.10; >=3/4 positive years; "
                            "top-3 eligible cells in a 2x2 adjacent block for every fold",
            "plateau_definition": "top three eligible cells fit in one adjacent 2x2 grid block",
            "bootstrap": {"draws": BOOTSTRAP_DRAWS, "unit": "session_date", "seed": BOOTSTRAP_SEED},
            "base_config": asdict(base), "data_provenance": provenance, "holdout_used": False,
            "code_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                            for name in ("strategy.py", "backtester.py", "risk.py", "strategy_b_sweep.py")}}
    (output / "spec.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    stable = result["stability"]
    stability_lines = ["# Training-surface stability", "",
                       "Plateau definition, fixed before reading results: the top three eligible training cells "
                       "must fit within one adjacent 2×2 OR/buffer block in **each** fold.", "",
                       "| Fold pair | Spearman rank correlation, 16 cell means | Top-three cell overlap |",
                       "| --- | ---: | ---: |"]
    for pair in ("1->2", "2->3", "3->4"):
        stability_lines.append(f"| {pair} | {stable['spearman_consecutive'][pair]:+.3f} | "
                               f"{stable['top3_overlap'][pair]}/3 |")
    stability_lines += ["", "| Fold | Positive-mean cells | Top three in adjacent 2×2 |",
                        "| ---: | ---: | --- |"]
    for fold in range(1, 5):
        stability_lines.append(f"| {fold} | {stable['positive_cells_by_fold'][fold]}/16 | "
                               f"{'yes' if stable['top3_adjacent_2x2_by_fold'][fold] else 'no'} |")
    stability_lines += ["", f"Selected path in one adjacent 2×2 block: "
                        f"{'yes' if stable['selected_path_adjacent_2x2'] else 'no'}.",
                        f"All-fold top-three plateau criterion: "
                        f"{'PASS' if stable['all_top3_are_plateaus'] else 'FAIL'}.", ""]
    (output / "stability.md").write_text("\n".join(stability_lines), encoding="utf-8")
    lines = ["# Strategy B — prespecified 4×4 training-only sweep", "",
             "**Pre-declared reading:** support requires all four: selected-path 2021–2024 mean R > 0; "
             "session-block bootstrap P(mean R ≤ 0) < 0.10; at least three of four unseen years positive; "
             "and the top three eligible training cells fit an adjacent 2×2 block in every fold. "
             "Otherwise the intended 08:20 New York XAUUSD ORB is unsupported by the 2018–2024 "
             "development data under this grid. No rescue search follows.", "",
             "The clock is verified as FTMO server wall time = New York + 7 h. The $100,000 "
             "simulated balance removes 0.01-lot sample censoring; $5,000 infeasibility is reported "
             "only as a statistic. Both directions remain enabled. The 2025+ holdout was not read.", "",
             "Cell format: training mean net R (trades); `*` selected; `!` ineligible (<100 trades).", ""]
    for fold in range(1, 5):
        frame = surface.loc[surface.fold == fold]
        lines += [f"## Fold {fold}: train {int(frame.train_start.iloc[0])}–"
                  f"{int(frame.train_end_inclusive.iloc[0])}, test {int(frame.test_year.iloc[0])}", "",
                  "| OR minutes | " + " | ".join(f"{b:.2f}" for b in CANDIDATE_BUFFERS) + " |",
                  "| ---: | " + " | ".join("---:" for _ in CANDIDATE_BUFFERS) + " |"]
        for length in RANGE_LENGTHS:
            cells = []
            for buffer in CANDIDATE_BUFFERS:
                row = frame.loc[(frame.range_minutes == length) & (np.isclose(frame.buffer, buffer))].iloc[0]
                value = "—" if pd.isna(row.mean_r) else f"{row.mean_r:+.3f} ({int(row.trades)})"
                cells.append(value + ("*" if row.selected else "") + ("!" if not row.eligible else ""))
            lines.append(f"| {length} | " + " | ".join(cells) + " |")
        lines.append("")
    lines += ["## Selected unseen path", "",
              "| Test | OR | Buffer | Train trades | Train mean R | Test trades | Test mean R | PF | Return | Max closed DD | Long R | Short R |",
              "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in path.itertuples(index=False):
        lines.append(f"| {row.test_year} | {row.selected_or_minutes} | {row.selected_buffer:.2f} | "
                     f"{row.training_trades} | {_fmt(row.training_mean_r,'+.4f')} | {row.test_trades} | "
                     f"{_fmt(row.test_mean_r,'+.4f')} | {_fmt(row.test_profit_factor,'.3f')} | "
                     f"{row.test_return_pct:+.2f}% | {row.test_max_closed_dd_pct:.2f}% | "
                     f"{_fmt(row.test_long_mean_r,'+.4f')} | {_fmt(row.test_short_mean_r,'+.4f')} |")
    combined, boot = result["combined"], result["bootstrap"]
    lines += ["", "## Combined unseen 2021–2024", "",
              f"Trades {combined['trades']}; mean R {_fmt(combined['mean_r'],'+.4f')}; median R "
              f"{_fmt(extra['median_r'],'+.4f')}; PF {_fmt(combined['profit_factor'],'.3f')}; "
              f"return {combined['return_pct']:+.2f}%; max closed DD "
              f"{combined['max_closed_dd_pct']:.2f}%; drawdown duration "
              f"{extra['max_closed_drawdown_duration_days']:.0f} calendar days; longest losing streak "
              f"{extra['longest_losing_streak']}; positive years {extra['positive_years']}/4.", "",
              f"Long: {extra['long_trades']} trades, mean R {_fmt(extra['long_mean_r'],'+.4f')}. "
              f"Short: {extra['short_trades']} trades, mean R {_fmt(extra['short_mean_r'],'+.4f')}. "
              "This asymmetry is recorded only as a future hypothesis; neither direction was disabled.", "",
              f"Session-date block bootstrap ({BOOTSTRAP_DRAWS:,} draws): 95% mean-R CI "
              f"[{boot['ci_low']:+.4f}, {boot['ci_high']:+.4f}], "
              f"P(mean R ≤ 0) = {boot['p_mean_le_zero']:.3f}.", "",
              "| Year | Return | Long trades / mean R | Short trades / mean R |",
              "| ---: | ---: | ---: | ---: |"]
    for year, row in extra["yearly"].items():
        long_r = "—" if row["long_mean_r"] is None else f"{row['long_mean_r']:+.4f}"
        short_r = "—" if row["short_mean_r"] is None else f"{row['short_mean_r']:+.4f}"
        lines.append(f"| {year} | {row['return_pct']:+.2f}% | {row['long_trades']} / {long_r} | "
                     f"{row['short_trades']} / {short_r} |")
    lines += ["", "See [training-surface stability](stability.md) for Spearman ranks, top-three "
              "overlap and the adjacent-cell test.", "",
              ("**Supported by development data under the pre-declared grid.**" if result["supported"] else
               "**The intended 08:20 New York XAUUSD ORB is unsupported by the 2018–2024 "
               "development data under the prespecified grid.**"), "",
              "This selected-path walk-forward remains development-period evidence. "
              "No 2025+ strategy outcome was accessed.", ""]
    report = output / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
