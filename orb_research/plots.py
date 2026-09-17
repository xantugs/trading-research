"""Offline-readable baseline plots built from saved trades and period returns."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_baseline_plots(trades: pd.DataFrame, monthly: pd.DataFrame,
                         yearly: pd.DataFrame, output_dir: str | Path, initial_equity: float) -> None:
    try:
        import plotly.express as px
        import plotly.graph_objects as go
    except ImportError as exc:
        raise RuntimeError('plots require: pip install -e ".[research-v2]"') from exc
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ordered = trades.sort_values("exit_time")
    equity = pd.Series([initial_equity, *ordered.equity_after.to_list()])
    timeline = [ordered.exit_time.iloc[0], *ordered.exit_time.to_list()]
    peak = equity.cummax()
    drawdown = (equity / peak - 1) * 100

    def save(figure, filename):
        figure.update_layout(template="plotly_dark", title_x=0.02)
        figure.write_html(out / filename, include_plotlyjs="directory", full_html=True)

    save(go.Figure(go.Scatter(x=timeline, y=equity, mode="lines", name="Closed equity"))
         .update_layout(title="Closed-trade equity", xaxis_title="UTC", yaxis_title="Account currency"),
         "equity_curve.html")
    save(go.Figure(go.Scatter(x=timeline, y=drawdown, mode="lines", fill="tozeroy", name="Drawdown"))
         .update_layout(title="Closed-trade drawdown", xaxis_title="UTC", yaxis_title="Percent"),
         "drawdown_curve.html")
    save(px.bar(monthly, x="period", y="return_pct", title="Monthly returns", labels={"return_pct": "%"}),
         "monthly_returns.html")
    save(px.bar(yearly, x="period", y="return_pct", title="Yearly returns", labels={"return_pct": "%"}),
         "yearly_returns.html")
    rolling = ordered[["exit_time", "R_result"]].copy()
    rolling["rolling_expectancy_r"] = rolling.R_result.rolling(50, min_periods=20).mean()
    save(px.line(rolling, x="exit_time", y="rolling_expectancy_r", title="Rolling mean R (50 trades)"),
         "rolling_expectancy.html")
    m = monthly.copy()
    percent = m.return_pct / 100
    m["rolling_sharpe"] = (percent.rolling(6, min_periods=6).mean() /
                            percent.rolling(6, min_periods=6).std() * 12 ** 0.5)
    save(px.line(m, x="period", y="rolling_sharpe", title="Rolling monthly Sharpe (6 months)"),
         "rolling_sharpe.html")
    excursion = ordered[["MAE", "MFE"]].melt(var_name="excursion", value_name="R")
    save(px.histogram(excursion, x="R", color="excursion", barmode="overlay", nbins=40,
                      title="MAE and MFE (R)") , "mae_mfe.html")
