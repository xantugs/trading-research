"""Interactive dark research workspace for the ORB bar engine."""
from __future__ import annotations

import hashlib
import html
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from orb import Params, infer_minutes, load_bars, resample, run_backtest
from orb.config import load_params

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TEAL, RED, BLUE = "#42d9bd", "#ff8296", "#78aaff"
SURFACE, INK, MUTED, GRID = "#101a29", "#e6edf7", "#99abc1", "#243247"
COLORS = [TEAL, BLUE, "#c7a1ff", "#ffc979", "#ff8296", "#7fd6ed", "#c6d679", "#e9aece"]
PLOT_CONFIG = {"displaylogo": False, "scrollZoom": False,
               "modeBarButtonsToRemove": ["lasso2d", "select2d"]}

PRESETS = {
    "Baseline": ({}, "Close confirmation, both directions, 1.5R target. Flat at 22:45."),
    "Tighter stop": ({"sl_ratio": 0.5}, "Stop halfway into the opening range. Keep an eye on stop-outs."),
    "2R target": ({"tp_ratio": 2.0}, "A wider target with the same entry and stop rules."),
    "Immediate breakout": ({"entry_mode": "immediate"}, "Enter on the first crossing. M1 cannot reveal exact tick order."),
    "Long + trend": ({"direction": "long", "filter_ma_period": 50}, "Longs above the H1 EMA(50). Includes a history warmup."),
    "Protect at 1R": ({"breakeven_at_r": 1.0}, "Move the stop to entry after a 1R excursion."),
    "Trail the move": ({"tp_ratio": 5.0, "trail_start_r": 1.0}, "Start trailing at 1R, at a distance of 1R."),
}
STRATEGY_FIELDS = (
    "entry_mode", "direction", "tp_ratio", "sl_ratio", "eod_close",
    "atr_period", "min_range_atr", "max_range_atr", "vol_lookback", "vol_spike_min",
    "filter_ma_period", "filter_tf_minutes", "filter_ma_method", "breakeven_at_r",
    "breakeven_lock_r", "trail_start_r", "trail_dist_r", "trail_step_r",
)

CSS = """
<style>
.stApp {background:radial-gradient(ellipse at 80% 0%, #112737 0, #090f19 45%); color:#e6edf7}
[data-testid="stHeader"] {background:rgba(9,15,25,.94)}
[data-testid="stSidebar"] {background:#0d1623; border-right:1px solid #243247}
[data-testid="stSidebar"] .block-container {padding-top:1.3rem}
.block-container {padding-top:4rem; padding-bottom:3rem; max-width:1600px}
h1 {font-size:2.05rem!important; letter-spacing:-.045em; font-weight:700!important}
h2 {font-size:1.35rem!important; letter-spacing:-.025em}
h3 {font-size:1.05rem!important}
[data-testid="stCaptionContainer"] {color:#99abc1}
[data-testid="stMetric"] {background:#101a29; border:1px solid #243247; border-radius:14px; padding:16px 18px}
[data-testid="stMetricLabel"] {color:#99abc1; font-size:.8rem}
[data-testid="stMetricValue"] {font-size:1.8rem; letter-spacing:-.04em}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:#243247!important; border-radius:14px}
[data-baseweb="tab-list"], [role="tablist"] {gap:22px; border-bottom:1px solid #243247}
[role="tab"] {font-size:.9rem; padding:12px 2px}
[role="tab"][aria-selected="true"] {color:#42d9bd!important}
[data-testid="stSidebar"] [data-testid="stExpander"] {background:#101a29}
[data-testid="stSidebar"] label {font-size:.82rem}
[data-testid="stSidebar"] h2 {font-size:1rem!important}
button[kind="primary"] {border:0; color:#071a16!important; font-weight:700; border-radius:10px}
button[kind="secondary"] {border-color:#30425a; border-radius:9px}
.orb-eyebrow {font:600 11px system-ui; text-transform:uppercase; letter-spacing:.16em; color:#42d9bd; margin:0 0 10px}
.orb-brand {font-size:25px; font-weight:800; letter-spacing:-1px; margin:0}
.orb-brand span {color:#42d9bd}
.orb-muted {color:#99abc1; font-size:12px; line-height:1.6}
.orb-source {display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin:3px 0 20px}
.orb-pill {display:inline-block; border:1px solid #30425a; background:#111f30; color:#bdcbe0; padding:5px 10px; border-radius:7px; font-size:11px}
.orb-pill.demo {color:#ffcf88; background:#2c251a; border-color:#615034}
.orb-summary {font-size:13px; color:#99abc1; margin:8px 0 12px}
.orb-summary strong {color:#e6edf7; font-weight:500}
@media(max-width:850px) {
 .block-container {padding-left:1rem; padding-right:1rem}
 h1 {font-size:1.6rem!important}
 [data-testid="stMetric"] {padding:12px}
 [data-testid="stMetricValue"] {font-size:1.5rem}
}
@media(max-width:1100px) {
 .st-key-kpis [data-testid="stHorizontalBlock"] {flex-wrap:wrap}
 .st-key-kpis [data-testid="stColumn"] {flex:1 1 calc(50% - 1rem); min-width:0}
}
</style>
"""


@st.cache_data(show_spinner=False)
def load_file(source: str | bytes, signature: str) -> pd.DataFrame:
    return load_bars(source)


def style(fig: go.Figure, height=310, ytitle="", legend=False) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="Inter, Segoe UI, sans-serif", color=MUTED, size=12),
        height=height, margin=dict(l=16, r=20, t=24, b=20), showlegend=legend,
        legend=dict(orientation="h", y=1.14, x=0), hovermode="x unified",
        hoverlabel=dict(bgcolor="#1b2c40", font_color=INK), bargap=.3,
        modebar=dict(bgcolor=SURFACE, color=MUTED, activecolor=TEAL),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=GRID, automargin=True)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID, automargin=True)
    if ytitle:
        fig.update_yaxes(title_text=ytitle)
    return fig


def plot(fig, key):
    st.plotly_chart(fig, width="stretch", theme=None, config=PLOT_CONFIG, key=key)


def initialize():
    if st.session_state.get("workspace_version") == 2:
        return
    path = os.environ.get("ORB_PARAMS_FILE")
    p = st.session_state.get("cfg") or (load_params(path) if path else load_params(ROOT / "configs/baseline.json"))
    for name, value in p.to_dict().items():
        st.session_state[f"p_{name}"] = value
    st.session_state.p_eod_on = p.eod_close is not None
    st.session_state.p_eod_text = p.eod_close or "22:45"
    st.session_state.signal_tf = int(os.environ.get("ORB_SIGNAL_TF", "15"))
    st.session_state.experiments = []
    st.session_state.workspace_version = 2


def apply_preset():
    default = Params(eod_close="22:45")
    overrides = PRESETS[st.session_state.preset][0]
    changed = replace(default, **overrides)
    for name in STRATEGY_FIELDS:
        st.session_state[f"p_{name}"] = getattr(changed, name)
    st.session_state.p_eod_on = changed.eod_close is not None
    st.session_state.p_eod_text = changed.eod_close or "22:45"


def number(label, field, minimum=0.0, step=0.1, fmt=None, help=None):
    value = st.session_state[f"p_{field}"]
    # Preserve valid configurations loaded by the launcher even beyond UI presets.
    kwargs = dict(min_value=minimum, step=step, key=f"p_{field}", help=help)
    if isinstance(value, int):
        kwargs["min_value"], kwargs["step"] = int(minimum), max(1, int(step))
    if fmt:
        kwargs["format"] = fmt
    return st.number_input(label, **kwargs)


def sidebar():
    with st.sidebar:
        st.markdown('<div class="orb-eyebrow">Strategy research</div><div class="orb-brand">ORB<span> / </span>Lab</div>', unsafe_allow_html=True)
        st.caption("Opening range. Every assumption visible.")
        paths = sorted(p.resolve() for p in DATA_DIR.rglob("*_M1.csv"))
        selected = os.environ.get("ORB_DATA_FILE")
        if selected and Path(selected).exists():
            selected = Path(selected).resolve()
            paths = [selected] + [p for p in paths if p != selected]
        choice = st.selectbox("Dataset", paths, index=0 if paths else None,
                              format_func=lambda p: p.stem.replace("SYNTHETIC_NAS100", "Synthetic NAS100"),
                              key="dataset")
        with st.expander("Upload your own bars"):
            upload = st.file_uploader("M1 or signal bars CSV", type=["csv", "txt"])
            st.caption("Timestamps must already use the strategy clock. Uploaded files stay in memory.")

        st.selectbox("Starting preset", list(PRESETS), key="preset", on_change=apply_preset)
        st.caption(PRESETS[st.session_state.preset][1])
        st.toggle("Auto-run changes", value=False, key="auto_run",
                  help="Recalculate when data or parameters change. Runs are only saved when you choose Save run.")
        run = st.button("Run backtest", type="primary", width="stretch")
        st.caption("Save interesting results in the workspace to compare them later.")

        st.subheader("Trade setup")
        st.radio("Direction", ["both", "long", "short"], horizontal=True,
                 key="p_direction", format_func=lambda v: v.title())
        st.selectbox("Entry confirmation", ["close", "immediate"], key="p_entry_mode",
                     format_func=lambda v: "Candle close" if v == "close" else "First price crossing")
        # Sliders expose the common research range; numeric extras never silently clamp.
        st.slider("Target · R", 0.1, max(5.0, float(st.session_state.p_tp_ratio)),
                  step=0.1, key="p_tp_ratio")
        st.slider("Stop · × opening range", 0.05, max(3.0, float(st.session_state.p_sl_ratio)),
                  step=0.05, key="p_sl_ratio",
                  help="1.0 places the stop at the opposite edge of the range.")
        st.slider("Risk · % equity", 0.1, max(10.0, float(st.session_state.p_risk_pct)),
                  step=0.1, key="p_risk_pct")

        with st.expander("Session & timing"):
            st.selectbox("Signal timeframe", [5, 15, 30, 60], key="signal_tf", format_func=lambda v: f"M{v}")
            a, b = st.columns(2)
            with a:
                st.number_input("Range hour", 0, 23, key="p_orb_hour")
            with b:
                st.number_input("Range minute", 0, 59, key="p_orb_minute")
            st.number_input("Entry cutoff hour", 1, 23, key="p_cutoff_hour")
            st.checkbox("Flat at end of session", key="p_eod_on")
            st.text_input("Close time · HH:MM", key="p_eod_text", disabled=not st.session_state.p_eod_on)
            st.caption("These are dataset-clock times. ny+7 maps 09:30 New York to 16:30 year-round.")

        with st.expander("Filters"):
            number("Daily ATR period", "atr_period", step=1)
            a, b = st.columns(2)
            with a:
                number("Min range / ATR", "min_range_atr")
            with b:
                number("Max range / ATR", "max_range_atr")
            number("Volume lookback", "vol_lookback", step=1)
            number("Required volume ratio", "vol_spike_min")
            number("Trend MA period", "filter_ma_period", step=1)
            st.selectbox("Trend timeframe", sorted(set([15, 30, 60, 240, 1440, st.session_state.p_filter_tf_minutes])),
                         key="p_filter_tf_minutes", format_func=lambda v: f"{v} minutes")
            st.radio("MA method", ["ema", "sma"], horizontal=True, key="p_filter_ma_method")
            st.caption("Zero disables a filter. Volume is a source-dependent proxy, not a session calendar.")

        with st.expander("Breakeven & trailing"):
            number("Breakeven trigger · R", "breakeven_at_r")
            number("Breakeven lock · R", "breakeven_lock_r")
            number("Trailing trigger · R", "trail_start_r")
            number("Trailing distance · R", "trail_dist_r")
            number("Trailing step · R", "trail_step_r")
            st.caption("Zero trigger disables management. Stops update per path bar in Python.")

        with st.expander("Costs & contract"):
            number("Assumed spread · price units", "spread")
            st.checkbox("Use recorded spread instead", key="p_use_data_spread")
            number("Commission / lot / side", "commission_per_lot")
            number("Starting balance", "initial_balance", minimum=1.0, step=100.0)
            number("Money / 1.0 price move / lot", "point_value", minimum=0.00001, step=0.01, fmt="%.5f")
            number("Symbol point size", "point", minimum=0.000001, step=0.00001, fmt="%.6f")
            number("Lot step", "lot_step", minimum=0.00001, step=0.01, fmt="%.5f")
            number("Minimum lot", "min_lot", minimum=0.00001, step=0.01, fmt="%.5f")
            number("Maximum lot", "max_lot", minimum=0.00001, step=1.0)
            st.selectbox("If SL and TP touch in one bar", ["sl_first", "tp_first"], key="p_same_bar_rule",
                         format_func=lambda v: "Stop first (conservative)" if v == "sl_first" else "Target first (optimistic)")
            st.caption("Minimum lot clamping can exceed the requested risk. Margin and financing are not modeled.")
        st.divider()
        st.caption("Python bar simulation · EA v1.30\n\nResearch results need broker reconciliation.")
    values = {field: st.session_state[f"p_{field}"] for field in Params().to_dict()}
    values["eod_close"] = st.session_state.p_eod_text if st.session_state.p_eod_on else None
    return choice, upload, st.session_state.signal_tf, Params(**values), run


def performance_chart(record, mode):
    result = record["result"]
    if mode == "Gross R":
        trades = result.trades.sort_values("exit_time")
        series = pd.concat([pd.Series([0.0], index=[result.equity.index[0]]),
                            pd.Series(trades.r.cumsum().values, index=trades.exit_time)])
        baseline, unit = 0, "R"
        dd = series - series.cummax()
    else:
        series = result.equity
        baseline, unit = result.params.initial_balance, "Balance"
        dd = (series / series.cummax() - 1) * 100
        if mode == "Return %":
            series = (series / baseline - 1) * 100
            baseline, unit = 0, "%"
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.75, .25], vertical_spacing=.08)
    fig.add_trace(go.Scatter(x=series.index, y=series.values, mode="lines", line=dict(color=TEAL, width=2.3),
                             name=mode, hovertemplate="%{y:,.2f}<extra>" + unit + "</extra>"), row=1, col=1)
    fig.add_hline(y=baseline, line_dash="dot", line_color="#596b80", row=1, col=1)
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy", fillcolor="rgba(255,130,150,.13)",
                             line=dict(color=RED, width=1.3), name="Drawdown",
                             hovertemplate="%{y:.2f}<extra>Drawdown</extra>"), row=2, col=1)
    style(fig, height=460)
    fig.update_yaxes(title_text=unit, row=1, col=1)
    fig.update_yaxes(title_text="DD · R" if mode == "Gross R" else "DD · %", row=2, col=1)
    fig.update_layout(uirevision=record["id"], dragmode="zoom")
    return fig


def signed_bars(labels, values, title="R"):
    fig = go.Figure(go.Bar(x=list(labels), y=list(values),
                          marker_color=[TEAL if v >= 0 else RED for v in values],
                          hovertemplate="%{x}<br>%{y:.2f}<extra>" + title + "</extra>"))
    return style(fig, ytitle=title, height=285)


def overview(record):
    result, trades = record["result"], record["result"].trades
    if trades.empty:
        st.info("No trades for this setup. Check the filters and session timing in Diagnostics.")
        return
    with st.container(border=True):
        a, b = st.columns([1, 1.25])
        a.subheader("Performance path")
        with b:
            mode = st.radio("Chart measure", ["Balance", "Return %", "Gross R"], horizontal=True,
                            label_visibility="collapsed", key="chart_measure")
        plot(performance_chart(record, mode), "performance_" + record["id"])
        st.caption("Drag to zoom · double-click to reset · hover for values. Drawdown uses closed trades.")
    a, b = st.columns(2)
    with a, st.container(border=True):
        st.subheader("Month by month")
        monthly = trades.set_index("exit_time").r.resample("MS").sum()
        plot(signed_bars(monthly.index.strftime("%b %Y"), monthly.values), "monthly_" + record["id"])
    with b, st.container(border=True):
        st.subheader("Where returns came from")
        group = st.selectbox("Break down by", ["weekday", "direction", "entry_hour", "reason"],
                            format_func=lambda x: {"weekday": "Weekday", "direction": "Direction",
                                                   "entry_hour": "Entry hour", "reason": "Exit reason"}[x])
        summary = result.by(group)
        labels = [f"{v:02d}:00" for v in summary.index] if group == "entry_hour" else summary.index
        plot(signed_bars(labels, summary.avg_r.values, "Average R"), "breakdown_" + record["id"])


def filtered_trades(trades, directions, reasons, dates):
    """Explorer filters do not rerun the strategy or change headline metrics."""
    if trades.empty:
        return trades
    subset = trades[trades.direction.isin(directions) & trades.reason.isin(reasons)]
    if len(dates) == 2:
        start, end = pd.Timestamp(dates[0]), pd.Timestamp(dates[1]) + pd.Timedelta(days=1)
        subset = subset[(subset.entry_time >= start) & (subset.entry_time < end)]
    return subset


def trade_chart(record, trade):
    start = pd.Timestamp(trade.orb_time) - pd.Timedelta(hours=1)
    finish = min(pd.Timestamp(trade.exit_time) + pd.Timedelta(hours=1), start + pd.Timedelta(days=3))
    candles = record["bars"].loc[start:finish]
    fig = go.Figure(go.Candlestick(
        x=candles.index, open=candles.open, high=candles.high, low=candles.low, close=candles.close,
        increasing_line_color=TEAL, decreasing_line_color=RED, name=f"M{record['tf']} candles",
    ))
    fig.add_hrect(y0=trade.orb_low, y1=trade.orb_high, fillcolor=TEAL, opacity=.07, line_width=0)
    fig.add_vrect(x0=trade.orb_time, x1=trade.orb_time + pd.Timedelta(minutes=record["tf"]),
                  fillcolor=TEAL, opacity=.13, line_width=0)
    for price, label, color in [(trade.sl, "Initial SL", RED), (trade.tp, "Target", BLUE),
                                (trade.entry, "Entry", "#f1d28b")]:
        fig.add_hline(y=price, line_dash="dot", line_color=color, annotation_text=label,
                      annotation_font_color=color, annotation_position="top left")
    for when, price, label, symbol, color in [
        (trade.entry_time, trade.entry, "Entry", "triangle-up" if trade.direction == "long" else "triangle-down", "#f1d28b"),
        (trade.exit_time, trade["exit"], "Exit · " + trade.reason, "x", INK),
    ]:
        if start <= when <= finish:
            fig.add_trace(go.Scatter(x=[when], y=[price], mode="markers", name=label,
                                    marker=dict(symbol=symbol, size=12, color=color),
                                    hovertemplate="%{x}<br>%{y:.2f}<extra>" + label + "</extra>"))
    style(fig, height=430)
    fig.update_layout(xaxis_rangeslider_visible=False, hovermode="closest")
    return fig


def trades_view(record):
    trades = record["result"].trades
    if trades.empty:
        st.info("This run has no trades to inspect.")
        return
    st.subheader("Explore the trades")
    st.caption("These filters affect this explorer and its CSV export only. Overview keeps the full run.")
    a, b, c = st.columns([1, 1, 1.5])
    with a:
        directions = st.multiselect("Direction filter", ["long", "short"], default=["long", "short"])
    with b:
        reasons = st.multiselect("Exit filter", sorted(trades.reason.unique()), default=sorted(trades.reason.unique()),
                                 key="exit_filter_" + record["id"])
    with c:
        dates = st.date_input("Entry dates", (trades.entry_time.min().date(), trades.entry_time.max().date()),
                              min_value=trades.entry_time.min().date(), max_value=trades.entry_time.max().date(),
                              key="trade_dates_" + record["id"])
    subset = filtered_trades(trades, directions, reasons, dates)
    st.caption(f"{len(subset)} of {len(trades)} trades shown")
    columns = ["entry_time", "direction", "entry", "sl", "tp", "exit_time", "exit", "reason", "r", "lots", "profit"]
    st.dataframe(subset[columns], hide_index=True, width="stretch", height=300,
                 column_config={"r": st.column_config.NumberColumn("Gross R", format="%.2f"),
                                "profit": st.column_config.NumberColumn("Net P/L", format="%.2f"),
                                "entry": st.column_config.NumberColumn("Entry", format="%.2f"),
                                "sl": st.column_config.NumberColumn("Initial SL", format="%.2f"),
                                "tp": st.column_config.NumberColumn("Target", format="%.2f"),
                                "exit": st.column_config.NumberColumn("Exit", format="%.2f"),
                                "lots": st.column_config.NumberColumn("Lots", format="%.3f"),
                                "direction": "Side", "reason": "Exit reason"})
    st.download_button("Export filtered trades", subset.to_csv(index=False), "orb_filtered_trades.csv", "text/csv")
    if subset.empty:
        st.info("No trades match these filters. Widen the dates or select another direction.")
        return
    with st.container(border=True):
        selected = st.selectbox(
            "Inspect a trade", list(subset.index),
            format_func=lambda i: f"{subset.loc[i, 'entry_time']:%d %b %Y %H:%M} · "
                                  f"{subset.loc[i, 'direction'].upper()} · {subset.loc[i, 'r']:+.2f} R",
            key="inspect_" + record["id"],
        )
        trade = subset.loc[selected]
        st.caption(f"Opening range {trade.orb_low:,.2f}–{trade.orb_high:,.2f} · "
                   f"Initial risk {trade.risk_dist:,.2f} · {trade.lots:g} lots · Net P/L {trade.profit:+,.2f}")
        plot(trade_chart(record, trade), "trade_candles_" + record["id"])
        st.caption(f"M{record['tf']} candles · shaded band = opening range · shaded candle = range capture. "
                   "SL shows the initial level, not later trailing changes. Long holds show the first three days.")


def comparison_frame(records):
    return pd.DataFrame([{
        "Run": r["name"], "Dataset": r["source"], "TF": f"M{r['tf']}",
        "Trades": r["result"].stats["trades"], "Return %": r["result"].stats["return_pct"],
        "Max DD %": r["result"].stats["max_dd_pct"], "Average R": r["result"].stats["avg_r"],
        "Win %": r["result"].stats["win_rate"] * 100, "Target R": r["result"].params.tp_ratio,
        "Stop ×": r["result"].params.sl_ratio,
    } for r in records])


def compare_view():
    records = st.session_state.experiments
    st.subheader("Compare your experiments")
    st.caption("Save runs above, change the setup, then save again. Up to eight snapshots are kept in this browser session.")
    if not records:
        st.info("Save your first run to start a comparison.")
        return
    by_id = {r["id"]: r for r in records}
    ids = st.multiselect("Overlay runs", list(by_id), default=list(by_id),
                         format_func=lambda i: by_id[i]["name"], key="overlay_" + "_".join(by_id))
    axis = st.radio("Align by", ["Calendar time", "Closed-trade number"], horizontal=True)
    fig = go.Figure()
    for i, run_id in enumerate(ids):
        record = by_id[run_id]
        series = record["result"].equity
        y = (series / record["result"].params.initial_balance - 1) * 100
        x = series.index if axis == "Calendar time" else list(range(len(series)))
        fig.add_trace(go.Scatter(x=x, y=y, name=record["name"], line=dict(color=COLORS[i], width=2),
                                 hovertemplate="%{y:.2f}%<extra>%{fullData.name}</extra>"))
    if ids:
        plot(style(fig, height=370, ytitle="Closed-balance return · %", legend=True), "comparison")
    else:
        st.info("Select at least one saved run.")
    st.caption("Overlays normalize starting balance. Different datasets, costs or periods are not equivalent experiments.")
    frame = comparison_frame(records)
    st.dataframe(frame, hide_index=True, width="stretch", column_config={
        "Return %": st.column_config.NumberColumn(format="%.2f"),
        "Max DD %": st.column_config.NumberColumn(format="%.2f"),
        "Average R": st.column_config.NumberColumn(format="%.3f"),
        "Win %": st.column_config.NumberColumn(format="%.1f"),
    })
    st.download_button("Export comparison", frame.to_csv(index=False), "orb_comparison.csv", "text/csv")
    with st.expander("Compare all assumptions"):
        settings = pd.DataFrame({r["name"]: r["result"].params.to_dict() for r in records}).astype(str)
        changed = settings.nunique(axis=1) > 1
        st.dataframe(settings.loc[changed] if changed.any() else settings, width="stretch")
        st.caption("Only differing settings are shown when multiple saved runs have different assumptions.")
    if st.button("Clear saved runs"):
        st.session_state.experiments = []
        st.rerun()


def diagnostics_view(record):
    result, days = record["result"], record["result"].days
    st.subheader("Before interpreting a result")
    if record["synthetic"]:
        st.info("Synthetic data is a software demo. It cannot establish a trading edge.")
    if not days.empty:
        quiet = days[(days.vol_ratio > 0) & (days.vol_ratio < 1.5)]
        if len(quiet):
            st.warning(f"{len(quiet)} of {len(days)} opening candles have a volume ratio below 1.5×. "
                       "Check the source clock and session alignment; a volume warning alone does not prove a DST error.")
        else:
            st.caption("No low-volume opening candles were flagged. This does not independently verify the session clock.")
        plot(signed_bars(days.date.astype(str), days.vol_ratio, "Volume ratio"), "volume_" + record["id"])
        st.dataframe(days, hide_index=True, width="stretch", height=280)
    st.caption(f"Input interval: {record['first']} to {record['last']} · {record['rows']:,} bars. "
               "The simulator uses the full available history, including indicator warmup.")
    st.markdown(
        "- Gross R excludes commission; money profit includes it.\n"
        "- Balance drawdown excludes unrealized intratrade losses.\n"
        "- Minimum lots can exceed the requested risk. No margin, financing or slippage model.\n"
        "- Immediate entries and stop management approximate tick ordering on bars.\n"
        "- END exits mark an unresolved trade at the last available close."
    )
    with st.expander("Exact parameters for this result"):
        st.json(result.params.to_dict())


def main():
    st.set_page_config(page_title="ORB Lab · Research workspace", page_icon="↗", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    initialize()
    choice, upload, tf, params, manual = sidebar()
    st.markdown('<div class="orb-eyebrow">Opening range breakout / research workspace</div>', unsafe_allow_html=True)
    st.title("Explore the opening move")
    st.caption("Shape a setup. Inspect every trade. Compare the assumptions.")
    try:
        if upload is not None:
            content = upload.getvalue()
            identity = hashlib.sha256(content).hexdigest()
            raw = load_file(content, identity)
            source = upload.name
        elif choice:
            stat = choice.stat()
            identity = f"{choice}:{stat.st_mtime_ns}:{stat.st_size}"
            raw = load_file(str(choice), identity)
            source = choice.name
        else:
            st.info("Choose a dataset or upload bars to get started. The launcher can generate demo data.")
            return
        have = infer_minutes(raw)
        if have > tf:
            raise ValueError(f"This dataset uses M{have} bars; choose a signal timeframe of at least M{have}.")
        params.validate(tf)
    except (ValueError, OSError, KeyError) as exc:
        st.error(f"Check the setup: {exc}")
        return
    synthetic = "SYNTHETIC" in source.upper()
    signature = hashlib.sha256(json.dumps([identity, tf, params.to_dict()], sort_keys=True).encode()).hexdigest()
    last = st.session_state.get("active_run")
    dirty = last is None or last["signature"] != signature
    if last is None or manual or (dirty and st.session_state.auto_run):
        try:
            with st.spinner("Simulating the setup…"):
                bars = resample(raw, tf) if have < tf else raw
                result = run_backtest(bars, params, path=raw if have < tf else None)
            last = dict(id=uuid4().hex[:10], signature=signature, result=result, bars=bars,
                        source=source, synthetic=synthetic, tf=tf, rows=len(raw), input_tf=have,
                        preset=st.session_state.preset,
                        first=str(raw.index[0]), last=str(raw.index[-1]))
            st.session_state.active_run = last
            dirty = False
        except ValueError as exc:
            st.error(f"Cannot run this setup: {exc}")
            return
    if dirty:
        st.info(f"Setup changed. Press Run backtest to apply it. Results below still use {last['source']} and the last executed settings.")
    pills = [("Synthetic demo" if last["synthetic"] else "Local dataset", "demo" if last["synthetic"] else ""),
             (last["source"], ""),
             (f"{pd.Timestamp(last['first']):%d %b %Y} — {pd.Timestamp(last['last']):%d %b %Y}", ""),
             (f"{last['rows']:,} input bars", "")]
    st.markdown('<div class="orb-source">' + "".join(
        f'<span class="orb-pill {kind}">{html.escape(label)}</span>' for label, kind in pills) + "</div>",
        unsafe_allow_html=True)
    result, p, stats = last["result"], last["result"].params, last["result"].stats
    st.markdown(
        f'<div class="orb-summary"><strong>{stats["trades"]} trades</strong> · '
        f'{stats.get("days_with_range", 0)} opening ranges · M{last["tf"]} · '
        f'{p.direction.title()} · {p.tp_ratio:g}R target · {p.risk_pct:g}% risk · '
        f'Profit factor <strong>{stats["profit_factor"]:.2f}</strong></div>', unsafe_allow_html=True)
    with st.container(key="kpis"):
        columns = st.columns(4)
        columns[0].metric("Net return", f'{stats["return_pct"]:+.1f}%', help=f'Final closed balance: {stats["final_balance"]:,.2f}')
        columns[1].metric("Win rate", f'{stats["win_rate"] * 100:.1f}%', help="Share of trades with positive gross R, before commission.")
        columns[2].metric("Average R", f'{stats["avg_r"]:+.3f} R', help="Average gross result in initial-risk units, before commission.")
        columns[3].metric("Max drawdown", f'{abs(stats["max_dd_pct"]):.1f}%', help="Largest closed-balance decline from a prior peak; excludes unrealized losses.")
    a, b, c = st.columns([1.5, 1, 1])
    with a:
        name = st.text_input("Run name", placeholder=f"{last.get('preset', 'Custom setup')} · M{last['tf']}", max_chars=60,
                             label_visibility="collapsed", key="run_name")
    with b:
        if st.button("Save run", width="stretch", disabled=len(st.session_state.experiments) >= 8):
            number_saved = st.session_state.get("saved_counter", 0) + 1
            st.session_state.saved_counter = number_saved
            saved = {**last, "id": uuid4().hex[:10], "name": f"{number_saved:02d} · {name.strip() or last.get('preset', 'Custom setup')}"}
            st.session_state.experiments.append(saved)
            st.toast("Run saved. Open Compare to overlay it.")
    with c:
        st.download_button("Export settings", json.dumps(p.to_dict(), indent=2), "orb_settings.json", "application/json", width="stretch")
    st.caption(f'{len(st.session_state.experiments)}/8 runs saved for this browser session. '
               "Settings exports contain the last executed setup.")
    tabs = st.tabs(["Overview", "Trades", "Compare", "Diagnostics"])
    with tabs[0]:
        overview(last)
    with tabs[1]:
        trades_view(last)
    with tabs[2]:
        compare_view()
    with tabs[3]:
        diagnostics_view(last)


if __name__ == "__main__":
    main()
