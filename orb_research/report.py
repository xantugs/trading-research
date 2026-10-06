"""Baseline metrics and inspectable artifacts; no parameter selection here."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config, holdout_note, serialise


def _max_run(values: pd.Series, positive: bool) -> int:
    longest = running = 0
    for value in values:
        running = running + 1 if (value > 0 if positive else value < 0) else 0
        longest = max(longest, running)
    return longest


def _period_returns(trades: pd.DataFrame, initial: float, frequency: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["period", "start_equity", "end_equity", "return_pct", "trades"])
    frame = trades.copy()
    frame["period"] = frame.exit_time.dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period(frequency)
    grouped = frame.groupby("period", sort=True).agg(end_equity=("equity_after", "last"),
                                                      trades=("PnL", "size"))
    full = pd.period_range(grouped.index.min(), grouped.index.max(), freq=frequency)
    grouped = grouped.reindex(full)
    grouped.end_equity = grouped.end_equity.ffill()
    grouped.trades = grouped.trades.fillna(0).astype(int)
    grouped["start_equity"] = grouped.end_equity.shift(1).fillna(initial)
    grouped["return_pct"] = (grouped.end_equity / grouped.start_equity - 1) * 100
    grouped.index.name = "period"
    return grouped.reset_index().assign(period=lambda data: data.period.astype(str))[
        ["period", "start_equity", "end_equity", "return_pct", "trades"]
    ]


def metrics(trades: pd.DataFrame, config: Config) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        raise ValueError("no trades; cannot infer a baseline edge")
    ordered = trades.sort_values("exit_time")
    equity = pd.Series([config.initial_equity, *ordered.equity_after.to_list()], dtype=float)
    peak = equity.cummax()
    drawdown = (equity / peak - 1) * 100
    equity_times = [pd.Timestamp(config.start, tz="UTC"), *ordered.exit_time.to_list()]
    episode_start = None
    durations = []
    for i, underwater in enumerate(equity < peak):
        if underwater and episode_start is None:
            episode_start = equity_times[i - 1]
        if not underwater and episode_start is not None:
            durations.append((equity_times[i] - episode_start).total_seconds() / 86_400)
            episode_start = None
    if episode_start is not None:
        durations.append((pd.Timestamp(config.development_end, tz="UTC") - episode_start)
                         .total_seconds() / 86_400)
    first = pd.Timestamp(config.start, tz="UTC")
    last = pd.Timestamp(config.development_end, tz="UTC")
    years = max((last - first).total_seconds() / 86_400 / 365.25, 1 / 365.25)
    monthly = _period_returns(ordered, config.initial_equity, "M")
    yearly = _period_returns(ordered, config.initial_equity, "Y")
    r = ordered.R_result
    winners = ordered.loc[ordered.PnL > 0]
    losers = ordered.loc[ordered.PnL < 0]
    gross_profit = float(winners.PnL.sum())
    gross_loss = -float(losers.PnL.sum())
    monthly_r = monthly.return_pct / 100
    sharpe = (float(monthly_r.mean() / monthly_r.std(ddof=1) * np.sqrt(12))
              if len(monthly_r) >= 3 and monthly_r.std(ddof=1) > 0 else None)
    downside = np.sqrt(np.mean(np.square(np.minimum(monthly_r, 0))))
    sortino = float(monthly_r.mean() / downside * np.sqrt(12)) if downside > 0 else None
    total_return = float((equity.iloc[-1] / equity.iloc[0] - 1) * 100)
    cagr = float(((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100)
    max_dd = -float(drawdown.min())
    profitable_years = float((yearly.return_pct > 0).mean()) if len(yearly) else 0.0
    profitable_months = float((monthly.return_pct > 0).mean()) if len(monthly) else 0.0
    by_direction = {}
    for direction, group in ordered.groupby("direction"):
        by_direction[direction] = {
            "trades": len(group), "mean_r": float(group.R_result.mean()),
            "net_pnl": float(group.PnL.sum()), "win_rate": float((group.PnL > 0).mean()),
        }
    summary = {
        "start": config.start, "end_exclusive": config.development_end,
        "holdout_start": config.holdout_start, "trades": len(ordered),
        "trades_per_year": len(ordered) / years,
        "trades_per_month": len(ordered) / (years * 12),
        "win_rate": float((ordered.PnL > 0).mean()),
        "expectancy_r": float(r.mean()),
        "expectancy_pct_equity_at_risk": float(r.mean() * config.risk_percent),
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "average_winner_r": float(winners.R_result.mean()) if len(winners) else None,
        "average_loser_r": float(losers.R_result.mean()) if len(losers) else None,
        "average_winner_pnl": float(winners.PnL.mean()) if len(winners) else None,
        "average_loser_pnl": float(losers.PnL.mean()) if len(losers) else None,
        "realized_reward_risk": (float(winners.PnL.mean() / -losers.PnL.mean())
                                 if len(winners) and len(losers) else None),
        "net_return_pct": total_return, "cagr_pct": cagr,
        "average_monthly_return_pct": float(monthly.return_pct.mean()),
        "median_monthly_return_pct": float(monthly.return_pct.median()),
        "monthly_volatility_pct": (float(monthly.return_pct.std(ddof=1))
                                   if len(monthly) >= 2 else None),
        "max_drawdown_pct": max_dd,
        "average_drawdown_pct": -float(drawdown.mean()),
        "max_drawdown_duration_days": max(durations, default=0.0),
        "average_drawdown_duration_days": float(np.mean(durations)) if durations else 0.0,
        "sharpe_monthly_annualized": sharpe, "sortino_monthly_annualized": sortino,
        "calmar": cagr / max_dd if max_dd else None,
        "max_consecutive_losses": _max_run(ordered.PnL, False),
        "max_consecutive_wins": _max_run(ordered.PnL, True),
        "best_month_pct": float(monthly.return_pct.max()),
        "best_month": str(monthly.loc[monthly.return_pct.idxmax(), "period"]),
        "worst_month_pct": float(monthly.return_pct.min()),
        "worst_month": str(monthly.loc[monthly.return_pct.idxmin(), "period"]),
        "best_year_pct": float(yearly.return_pct.max()),
        "best_year": str(yearly.loc[yearly.return_pct.idxmax(), "period"]),
        "worst_year_pct": float(yearly.return_pct.min()),
        "worst_year": str(yearly.loc[yearly.return_pct.idxmin(), "period"]),
        "profitable_month_fraction": profitable_months,
        "profitable_year_fraction": profitable_years,
        "ambiguous_m1_exits": int(ordered.ambiguous_m1.sum()),
        "modeled_commission_paid": float(ordered.commission.sum()),
        "by_direction": by_direction,
    }
    return summary, monthly, yearly


def _fmt(number: float | None, digits: int = 2) -> str:
    return "n/a" if number is None else f"{number:.{digits}f}"


def write_baseline(trades: pd.DataFrame, diagnostics: dict, provenance: dict, config: Config) -> Path:
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary, monthly, yearly = metrics(trades, config)
    trades.to_csv(out / "trades.csv", index=False)
    monthly.to_csv(out / "monthly_returns.csv", index=False)
    yearly.to_csv(out / "yearly_returns.csv", index=False)
    (out / "config_used.json").write_text(json.dumps(serialise(config), indent=2), encoding="utf-8")
    (out / "data_provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    (out / "diagnostics.json").write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    direction_lines = [
        f"| {name} | {group['trades']} | {_fmt(group['win_rate'] * 100)}% | "
        f"{_fmt(group['mean_r'])} | {_fmt(group['net_pnl'])} |"
        for name, group in summary["by_direction"].items()
    ]
    years = [f"| {row.period} | {row.trades} | {_fmt(row.return_pct)}% |"
             for row in yearly.itertuples(index=False)]
    report = f"""# XAUUSD ORB baseline — development data only

Data source: `{provenance.get('company', 'unknown')}` / `{provenance.get('server', 'unknown')}` /
`{provenance.get('symbol', 'unknown')}`. Period: {config.start} to {config.development_end}
(exclusive). {holdout_note(config)}.

| Metric | Result |
| --- | ---: |
| Trades | {summary['trades']} |
| Win rate | {_fmt(summary['win_rate'] * 100)}% |
| Mean net R/trade | {_fmt(summary['expectancy_r'], 3)} |
| Profit factor | {_fmt(summary['profit_factor'])} |
| Net return | {_fmt(summary['net_return_pct'])}% |
| Average monthly return | {_fmt(summary['average_monthly_return_pct'])}% |
| Maximum closed-equity drawdown | {_fmt(summary['max_drawdown_pct'])}% |
| Ambiguous M1 exits resolved stop-first | {summary['ambiguous_m1_exits']} |
| Modeled commission paid | ${_fmt(summary['modeled_commission_paid'])} |

## Direction

| Direction | Trades | Win rate | Mean R | Net P/L |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(direction_lines)}

## Year

| Year | Trades | Return |
| --- | ---: | ---: |
{chr(10).join(years)}

## Interpretation limits

- M5 close-confirmed entries use the next M1 open; M1 high/low determine exits.
- M1 bars do not reveal the order of stop and target touches within a minute;
  simultaneous touches resolve to the stop. MAE/MFE exclude the unknown price
  path after an intraminute exit.
- The recorded MT5 bar spread is an approximation of the actual entry/exit
  spread. Fallback spread is used when the field is zero. Slippage and
  commission come from the configuration and require broker calibration.
- Commission model: {config.commission_rate_pct_side}% of entry/exit notional
  per side plus ${config.commission_per_lot_side} per lot per side. Historical
  FTMO account charges have not been independently verified for every year.
- Drawdown is measured at closed trades, not intratrade equity or a firm's
  daily-limit reset. This report is not a prop-firm pass estimate.
- This is one baseline configuration, not evidence of a robust edge. Do not
  tune against data you will later call out-of-sample.
"""
    path = out / "summary_report.md"
    path.write_text(report, encoding="utf-8")
    return path
