"""MetaTrader-style strategy tester report computed from a run's closed-trade ledger.

The statistics follow the MT5 Strategy Tester's definitions where the ledger supports
them: balance drawdowns, recovery factor, AHPR/GHPR, Sharpe on per-trade returns,
linear-regression fit of the balance curve, the runs-test Z-score and win/loss streaks.
MT5's equity drawdown needs tick-by-tick marking of every open position; here it is a
worst-case estimate that puts every position open at a trade's entry at its own maximum
adverse excursion at once.  Margin level and deposit load are not modelled.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
REQUIRED = ("timestamp", "exit_time", "direction", "PnL", "MAE", "MFE", "estimated_risk")


def _streaks(pnl: np.ndarray) -> dict:
    """Win and loss runs in close order, as MT5 counts them (a trade at 0 counts as a loss)."""
    runs = []
    for value in pnl:
        win = value > 0
        if runs and runs[-1][0] == win:
            runs[-1][1] += 1
            runs[-1][2] += value
        else:
            runs.append([win, 1, value])
    wins = [run for run in runs if run[0]]
    losses = [run for run in runs if not run[0]]

    def longest(group):
        return max(group, key=lambda run: (run[1], abs(run[2])), default=None)

    def costliest(group, sign):
        return max(group, key=lambda run: sign * run[2], default=None)

    def pair(run):
        return None if run is None else {"count": run[1], "money": run[2]}

    return {
        "max_consecutive_wins": pair(longest(wins)),
        "max_consecutive_losses": pair(longest(losses)),
        "maximal_consecutive_profit": pair(costliest(wins, 1)),
        "maximal_consecutive_loss": pair(costliest(losses, -1)),
        "average_consecutive_wins": float(np.mean([run[1] for run in wins])) if wins else None,
        "average_consecutive_losses": float(np.mean([run[1] for run in losses])) if losses else None,
        "runs": len(runs),
    }


def _balance_drawdown(balance: np.ndarray) -> dict:
    """Absolute, maximal (largest money fall) and relative (largest % fall) drawdowns."""
    peaks = np.maximum.accumulate(balance)
    money = peaks - balance
    percent = np.divide(money, peaks, out=np.zeros_like(money), where=peaks > 0) * 100
    i_money, i_pct = int(money.argmax()), int(percent.argmax())
    return {
        "absolute": float(max(0.0, balance[0] - balance.min())),
        "maximal": float(money[i_money]), "maximal_pct": float(percent[i_money]),
        "relative_pct": float(percent[i_pct]), "relative": float(money[i_pct]),
    }


def _z_score(pnl: np.ndarray, runs: int) -> tuple[float | None, float | None]:
    """Runs test: negative Z means streaks cluster more than chance; |Z| > 2 is notable."""
    n = len(pnl)
    wins = int((pnl > 0).sum())
    p = 2 * wins * (n - wins)
    if n < 3 or p <= n:
        return None, None
    z = (n * (runs - 0.5) - p) / math.sqrt(p * (p - n) / (n - 1))
    confidence = math.erf(abs(z) / math.sqrt(2)) * 100
    return z, confidence


def _correlation(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _by(values: pd.Series, labels: tuple, pnl: pd.Series) -> list[dict]:
    rows = []
    for index, label in enumerate(labels):
        mask = values == index
        rows.append({"label": label, "entries": int(mask.sum()),
                     "profit": float(pnl[mask & (pnl > 0)].sum()),
                     "loss": float(-pnl[mask & (pnl < 0)].sum())})
    return rows


def _duration(hours: float) -> str:
    seconds = int(round(hours * 3600))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def tester_report(trades: pd.DataFrame, initial_equity: float,
                  timezone: str = "America/New_York") -> dict:
    missing = [column for column in REQUIRED if column not in trades]
    if missing:
        raise ValueError(f"trade ledger is missing {', '.join(missing)}")
    if trades.empty:
        return {"trades": 0}
    frame = trades.copy()
    # ISO8601: tick-level ledgers mix "…:47+00:00" and "…:47.123+00:00" in one column.
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True, format="ISO8601")
    frame["exit_time"] = pd.to_datetime(frame.exit_time, utc=True, format="ISO8601")
    frame["financing"] = frame.get("financing", 0.0)
    closed = frame.sort_values("exit_time", kind="stable").reset_index(drop=True)
    pnl = closed.PnL.to_numpy(dtype=float)
    balance = np.concatenate([[initial_equity], initial_equity + np.cumsum(pnl)])
    n = len(pnl)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_profit, gross_loss = float(wins.sum()), float(losses.sum())
    streaks = _streaks(pnl)
    drawdown = _balance_drawdown(balance)
    hpr = balance[1:] / balance[:-1]
    ahpr = float(hpr.mean())
    ghpr = float((balance[-1] / balance[0]) ** (1 / n)) if balance[-1] > 0 else None
    sharpe = float((ahpr - 1) / hpr.std(ddof=1)) if n > 1 and hpr.std(ddof=1) > 0 else None
    index = np.arange(len(balance), dtype=float)
    slope, intercept = np.polyfit(index, balance, 1) if len(balance) > 2 else (0.0, balance[0])
    residuals = balance - (slope * index + intercept)
    lr_error = float(math.sqrt((residuals ** 2).sum() / (len(balance) - 2))) if len(balance) > 2 else None
    z, z_confidence = _z_score(pnl, streaks["runs"])

    # Excursions in account money; MAE is shown negative, as MT5 plots it.
    mfe = (frame.MFE * frame.estimated_risk).to_numpy(dtype=float)
    mae = -(frame.MAE * frame.estimated_risk).to_numpy(dtype=float)
    profit = frame.PnL.to_numpy(dtype=float)
    hold = ((frame.exit_time - frame.timestamp).dt.total_seconds() / 3600).to_numpy(dtype=float)

    # Worst-case equity: realised balance at each entry minus the full MAE of every
    # position open at that moment (this one included).
    def nanoseconds(series):
        return series.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("int64")

    opened, closes = nanoseconds(frame.timestamp), nanoseconds(frame.exit_time)
    booked = np.searchsorted(nanoseconds(closed.exit_time), opened, side="left")
    open_mae = np.array([-mae[(opened <= t) & (closes >= t)].sum() for t in opened])
    peaks = np.maximum.accumulate(balance)[booked]
    depth = peaks - (balance[booked] - open_mae)
    worst = int(depth.argmax())
    trough_dd = float(max(0.0, depth[worst]))
    trough_dd_pct = float(trough_dd / peaks[worst] * 100) if trough_dd else 0.0

    local_entry = frame.timestamp.dt.tz_convert(timezone)
    longs, shorts = frame[frame.direction == "long"], frame[frame.direction == "short"]

    def won(group):
        return float((group.PnL > 0).mean() * 100) if len(group) else None

    stats = {
        "initial_deposit": float(initial_equity), "final_balance": float(balance[-1]),
        "total_net_profit": float(pnl.sum()), "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": gross_profit / -gross_loss if gross_loss < 0 else None,
        "expected_payoff": float(pnl.mean()),
        "recovery_factor": float(pnl.sum() / drawdown["maximal"]) if drawdown["maximal"] > 0 else None,
        "sharpe_ratio": sharpe, "ahpr": ahpr, "ghpr": ghpr,
        "lr_correlation": _correlation(index, balance), "lr_standard_error": lr_error,
        "z_score": z, "z_confidence_pct": z_confidence,
        "balance_drawdown": drawdown,
        "equity_drawdown_estimate": {"maximal": trough_dd, "maximal_pct": trough_dd_pct},
        "total_trades": n, "total_deals": 2 * n,
        "short_trades": len(shorts), "short_won_pct": won(shorts),
        "long_trades": len(longs), "long_won_pct": won(longs),
        "profit_trades": len(wins), "profit_trades_pct": len(wins) / n * 100,
        "loss_trades": len(losses), "loss_trades_pct": len(losses) / n * 100,
        "largest_profit_trade": float(wins.max()) if len(wins) else None,
        "largest_loss_trade": float(losses.min()) if len(losses) else None,
        "average_profit_trade": float(wins.mean()) if len(wins) else None,
        "average_loss_trade": float(losses.mean()) if len(losses) else None,
        **{key: value for key, value in streaks.items() if key != "runs"},
        "holding_time": {"min": _duration(hold.min()), "max": _duration(hold.max()),
                         "average": _duration(hold.mean())},
        "commission_paid": float(frame.get("commission", pd.Series(0.0)).sum()),
        "financing_paid": float(frame.financing.sum()),
    }
    return {
        "trades": n, "stats": stats,
        # The deposit at the first entry, then the balance after each close.
        "balance_curve": [[stamp.isoformat(), float(value)] for stamp, value in
                          zip([frame.timestamp.min(), *closed.exit_time], balance)],
        "by_hour": _by(local_entry.dt.hour, tuple(f"{hour:02d}" for hour in range(24)), frame.PnL),
        "by_weekday": _by(local_entry.dt.weekday, WEEKDAYS[:5], frame.PnL),
        "by_month": _by(local_entry.dt.month - 1, MONTHS, frame.PnL),
        "scatter": [{"profit": round(float(p), 2), "mfe": round(float(f), 2), "mae": round(float(a), 2),
                     "hold_hours": round(float(h), 3)}
                    for p, f, a, h in zip(profit, mfe, mae, hold)],
        "correlations": {"profit_mfe": _correlation(profit, mfe), "profit_mae": _correlation(profit, mae),
                         "mfe_mae": _correlation(mfe, mae), "profit_hold": _correlation(profit, hold)},
        "timezone": timezone,
    }
