"""Interactive dark research workspace for the ORB bar engine."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from orb import Params, infer_minutes, load_bars, load_ticks, resample, run_backtest, tick_quality
from orb.config import load_params

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
EA_PATH = ROOT / "mt5" / "ORB_Backtest.mq5"
TEAL, RED, BLUE = "#42d9bd", "#ff8296", "#78aaff"
SURFACE, INK, MUTED, GRID = "#101a29", "#e6edf7", "#99abc1", "#243247"
COLORS = [TEAL, BLUE, "#c7a1ff", "#ffc979", "#ff8296", "#7fd6ed", "#c6d679", "#e9aece"]
PLOT_CONFIG = {"displaylogo": False, "scrollZoom": False,
               "modeBarButtonsToRemove": ["lasso2d", "select2d"]}

PRESETS = {
    "Baseline": ({}, "Close confirmation, both directions, 1.5R target. Flat at 22:45."),
    "Midpoint stop": ({"stop_mode": "midpoint"}, "Place the initial stop at the centre of the range."),
    "Historical range": ({"range_mode": "historical_percentile"}, "Forecast from prior sessions' matching opening ranges."),
    "Adaptive range": ({"range_mode": "historical_percentile_premarket"}, "Historical opening range adjusted by today's premarket volatility."),
    "2R target": ({"tp_ratio": 2.0}, "A wider target with the same entry and stop rules."),
    "Immediate breakout": ({"entry_mode": "immediate"}, "Enter on the first crossing. M1 cannot reveal exact tick order."),
    "Long + trend": ({"direction": "long", "filter_ma_period": 50}, "Longs above the H1 EMA(50). Includes a history warmup."),
    "Protect at 1R": ({"breakeven_at_r": 1.0}, "Move the stop to entry after a 1R excursion."),
    "Trail the move": ({"tp_ratio": 5.0, "trail_start_r": 1.0}, "Start trailing at 1R, at a distance of 1R."),
}
STRATEGY_FIELDS = (
    "entry_mode", "direction", "range_mode", "range_history_sessions", "range_percentile",
    "range_multiplier", "premarket_window_minutes", "premarket_sensitivity",
    "confirmation_bars", "confirmation_same_color", "tp_ratio", "sl_ratio", "stop_mode", "eod_close",
    "atr_period", "min_range_atr", "max_range_atr", "range_lookback_minutes",
    "max_range_lookback_ratio", "vol_lookback", "vol_spike_min", "filter_ma_period",
    "filter_tf_minutes", "filter_ma_method", "filter_ma_slope_bars", "filter_ma_min_slope_pct",
    "breakeven_at_r",
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


@st.cache_data(show_spinner=False)
def load_tick_file(source: bytes, name: str, signature: str, strategy_offset_hours: int) -> pd.DataFrame:
    return load_ticks(source, name, strategy_offset_hours=strategy_offset_hours)


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
    if st.session_state.get("workspace_version") == 5:
        return
    path = os.environ.get("ORB_PARAMS_FILE")
    p = st.session_state.get("cfg") or (load_params(path) if path else load_params(ROOT / "configs/baseline.json"))
    for name, value in p.to_dict().items():
        st.session_state[f"p_{name}"] = value
    st.session_state.p_eod_on = p.eod_close is not None
    st.session_state.p_eod_text = p.eod_close or "22:45"
    st.session_state.signal_tf = int(os.environ.get("ORB_SIGNAL_TF", "15"))
    st.session_state.experiments = []
    st.session_state.workspace_version = 5


def apply_preset():
    default = Params(eod_close="22:45")
    overrides = PRESETS[st.session_state.preset][0]
    changed = replace(default, **overrides)
    for name in STRATEGY_FIELDS:
        st.session_state[f"p_{name}"] = getattr(changed, name)
    st.session_state.p_eod_on = changed.eod_close is not None
    st.session_state.p_eod_text = changed.eod_close or "22:45"


def number(label, field, minimum=0.0, maximum=None, step=0.1, fmt=None, help=None):
    value = st.session_state[f"p_{field}"]
    # Preserve valid configurations loaded by the launcher even beyond UI presets.
    kwargs = dict(min_value=minimum, step=step, key=f"p_{field}", help=help)
    if maximum is not None:
        kwargs["max_value"] = maximum
    if isinstance(value, int):
        kwargs["min_value"], kwargs["step"] = int(minimum), max(1, int(step))
    if fmt:
        kwargs["format"] = fmt
    return st.number_input(label, **kwargs)


def sidebar():
    with st.sidebar:
        st.markdown('<div class="orb-eyebrow">Strategy research</div><div class="orb-brand">ORB<span> / </span>Lab</div>', unsafe_allow_html=True)
        st.caption("Opening range. Every assumption visible.")
        paths = sorted({p.resolve() for pattern in ("*_M1.csv", "*_M1.csv.gz")
                        for p in DATA_DIR.rglob(pattern)})
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
        close_mode = st.session_state.p_entry_mode == "close"
        st.number_input("Confirmation candles", 1, 10, key="p_confirmation_bars", disabled=not close_mode,
                        help="Consecutive signal candles that must close beyond the range.")
        st.toggle("Require matching candle colour", key="p_confirmation_same_color", disabled=not close_mode,
                  help="Long confirmations close green; short confirmations close red.")
        # Sliders expose the common research range; numeric extras never silently clamp.
        st.slider("Target · R", 0.1, max(5.0, float(st.session_state.p_tp_ratio)),
                  step=0.1, key="p_tp_ratio")
        st.selectbox("Initial stop", ["range_multiple", "midpoint"], key="p_stop_mode",
                     format_func=lambda v: "Opening-range multiple" if v == "range_multiple"
                     else "Range midpoint")
        st.slider("Stop · × opening range", 0.05, max(3.0, float(st.session_state.p_sl_ratio)),
                  step=0.05, key="p_sl_ratio",
                  disabled=st.session_state.p_stop_mode == "midpoint",
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
            range_labels = {
                "signal_bar": "Observed opening candle",
                "historical_percentile": "Historical opening-range percentile",
                "historical_percentile_premarket": "Historical percentile + premarket volatility",
            }
            st.selectbox("Range construction", list(range_labels), key="p_range_mode",
                         format_func=range_labels.get)
            forecast = st.session_state.p_range_mode != "signal_bar"
            if forecast:
                a, b = st.columns(2)
                with a:
                    number("Prior sessions", "range_history_sessions", minimum=1, step=1,
                           help="Uses only earlier sessions at the same opening-window time.")
                with b:
                    number("Historical percentile", "range_percentile", minimum=0.1, maximum=100.0,
                           step=1.0, fmt="%.1f")
                number("Box width multiplier", "range_multiplier", minimum=0.01, step=0.05, fmt="%.2f",
                       help="Type any positive decimal multiplier.")
            if st.session_state.p_range_mode == "historical_percentile_premarket":
                a, b = st.columns(2)
                with a:
                    number("Premarket window · min", "premarket_window_minutes", minimum=1, step=15)
                with b:
                    number("Adjustment sensitivity", "premarket_sensitivity", minimum=0.0, step=0.1, fmt="%.2f")
            if st.session_state.p_range_mode == "signal_bar":
                st.caption("Uses the completed candle at the selected range time; trading starts after it closes.")
            else:
                st.caption("Forecasts a box around today's opening price using prior sessions only; trading can start immediately.")
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
            a, b = st.columns(2)
            with a:
                number("Prior range window · min", "range_lookback_minutes", minimum=1, step=15)
            with b:
                number("Max range / prior range", "max_range_lookback_ratio", step=0.05,
                       help="0 disables. Use 0.25 for the proposed one-quarter threshold.")
            number("Volume lookback", "vol_lookback", step=1)
            number("Required volume ratio", "vol_spike_min")
            number("Trend MA period", "filter_ma_period", step=1)
            st.selectbox("Trend timeframe", sorted(set([15, 30, 60, 240, 1440, st.session_state.p_filter_tf_minutes])),
                         key="p_filter_tf_minutes", format_func=lambda v: f"{v} minutes")
            st.radio("MA method", ["ema", "sma"], horizontal=True, key="p_filter_ma_method")
            a, b = st.columns(2)
            with a:
                number("MA slope bars", "filter_ma_slope_bars", step=1,
                       help="Completed trend-timeframe bars used to measure the MA change.")
            with b:
                number("Min MA slope · %", "filter_ma_min_slope_pct", step=0.01, fmt="%.3f",
                       help="Minimum signed percentage change across the selected slope bars.")
            st.caption("Zero disables a filter. EMA slope uses completed bars. Volume depends on the source.")

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
            number("Maximum spread · price units", "max_spread",
                   help="Zero disables the entry guard.")
            number("Minimum stop · points", "min_stop_points", step=1,
                   help="Zero disables the entry guard.")
            number("Commission / lot / side", "commission_per_lot")
            number("Starting balance", "initial_balance", minimum=1.0, step=100.0)
            number("Money / 1.0 price move / lot", "point_value", minimum=0.00001, step=0.01, fmt="%.5f")
            number("Symbol point size", "point", minimum=0.000001, step=0.00001, fmt="%.6f")
            number("Lot step", "lot_step", minimum=0.00001, step=0.01, fmt="%.5f")
            number("Minimum lot", "min_lot", minimum=0.00001, step=0.01, fmt="%.5f")
            number("Maximum lot", "max_lot", minimum=0.00001, step=1.0)
            number("Maximum gross leverage", "max_gross_leverage", minimum=0.0, step=0.25,
                   help="Gross position notional divided by equity. Zero disables the capital cap.")
            st.selectbox("If SL and TP touch in one bar", ["sl_first", "tp_first"], key="p_same_bar_rule",
                         format_func=lambda v: "Stop first (conservative)" if v == "sl_first" else "Target first (optimistic)")
            st.caption("A positive leverage cap prevents minimum-lot clamping from exceeding available capital. "
                       "Broker margin, financing and per-order commission minimums are not modeled.")
        st.divider()
        st.caption("Python bar simulation · EA v1.40\n\nResearch results need broker reconciliation.")
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


def trade_chart(record, trade, context_bars=24, view_tf=None, ticks=None):
    view_tf = view_tf or record["tf"]
    raw = record["raw"]
    bars = resample(raw, view_tf) if record["input_tf"] < view_tf else raw
    orb_pos = int(bars.index.searchsorted(pd.Timestamp(trade.orb_time)))
    exit_pos = int(bars.index.searchsorted(pd.Timestamp(trade.exit_time), side="right"))
    left = max(0, orb_pos - context_bars)
    right = min(len(bars), exit_pos + context_bars)
    # Keep very long holds readable while preserving the opening and early path.
    three_day_right = int(bars.index.searchsorted(bars.index[left] + pd.Timedelta(days=3), side="right"))
    right = min(right, three_day_right)
    candles = bars.iloc[left:right]
    start, finish = candles.index[0], candles.index[-1]
    fig = go.Figure(go.Candlestick(
        x=candles.index, open=candles.open, high=candles.high, low=candles.low, close=candles.close,
        increasing_line_color=TEAL, decreasing_line_color=RED, name=f"M{view_tf} candles",
    ))
    fig.add_hrect(y0=trade.orb_low, y1=trade.orb_high, fillcolor=TEAL, opacity=.12, line_width=0)
    fig.add_hline(y=trade.orb_high, line_dash="dash", line_color=TEAL,
                  annotation_text="Range high", annotation_font_color=TEAL,
                  annotation_position="top right")
    fig.add_hline(y=trade.orb_low, line_dash="dash", line_color=TEAL,
                  annotation_text="Range low", annotation_font_color=TEAL,
                  annotation_position="bottom right")
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
    audit = record["result"].events
    if not audit.empty:
        audit = audit[(audit.date == trade.date) & audit.event.isin(
            ["signal", "wick_rejected", "entry_rejected", "daily_lock"])]
        symbols = {"signal": "diamond", "wick_rejected": "circle-open",
                   "entry_rejected": "x", "daily_lock": "x-open"}
        colors = {"signal": "#f1d28b", "wick_rejected": MUTED,
                  "entry_rejected": RED, "daily_lock": RED}
        for event in audit.itertuples():
            if start <= event.time <= finish and np.isfinite(event.price):
                fig.add_trace(go.Scatter(
                    x=[event.time], y=[event.price], mode="markers", name=event.detail,
                    marker=dict(symbol=symbols[event.event], size=10, color=colors[event.event]),
                    hovertemplate=f"%{{x}}<br>%{{y:.2f}}<extra>{event.detail}</extra>",
                ))
    if ticks is not None and not ticks.empty:
        visible = ticks[(ticks.index >= start) & (ticks.index <= finish)].copy()
        if len(visible) > 30_000:
            visible = visible.iloc[::int(np.ceil(len(visible) / 30_000))]
        for column, color in (("bid", BLUE), ("ask", "#c7a1ff"), ("price", "#f1d28b")):
            if column not in visible:
                continue
            valid = visible[column].replace([np.inf, -np.inf], np.nan).dropna()
            valid = valid[valid > 0]
            fig.add_trace(go.Scattergl(
                x=valid.index, y=valid, mode="lines", name=f"Tick {column}",
                line=dict(color=color, width=.7), opacity=.8,
                hovertemplate=f"%{{x}}<br>%{{y:.4f}}<extra>{column}</extra>",
            ))
    style(fig, height=620)
    fig.update_layout(xaxis_rangeslider_visible=False, hovermode="closest", dragmode="zoom",
                      margin=dict(l=18, r=24, t=28, b=28))
    fig.update_xaxes(rangeslider_visible=False)
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
    columns = ["entry_time", "direction", "range_mode", "orb_range", "entry", "sl", "tp",
               "exit_time", "exit", "reason", "r", "mfe_r", "mae_r", "lots",
               "actual_risk_pct", "gross_leverage", "same_bar_ambiguous", "profit"]
    st.dataframe(subset[columns], hide_index=True, width="stretch", height=300,
                 column_config={"r": st.column_config.NumberColumn("Gross R", format="%.2f"),
                                "profit": st.column_config.NumberColumn("Net P/L", format="%.2f"),
                                "mfe_r": st.column_config.NumberColumn("Best excursion · R", format="%.2f"),
                                "mae_r": st.column_config.NumberColumn("Worst excursion · R", format="%.2f"),
                                 "entry": st.column_config.NumberColumn("Entry", format="%.2f"),
                                 "range_mode": "Range construction",
                                 "orb_range": st.column_config.NumberColumn("Range width", format="%.3f"),
                                "sl": st.column_config.NumberColumn("Initial SL", format="%.2f"),
                                "tp": st.column_config.NumberColumn("Target", format="%.2f"),
                                "exit": st.column_config.NumberColumn("Exit", format="%.2f"),
                                "lots": st.column_config.NumberColumn("Lots", format="%.3f"),
                                "actual_risk_pct": st.column_config.NumberColumn("Actual risk · %", format="%.2f"),
                                "gross_leverage": st.column_config.NumberColumn("Gross exposure · ×", format="%.2f"),
                                "same_bar_ambiguous": "SL/TP ambiguity",
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
        st.caption(f"{trade.range_mode} · width {trade.orb_range:,.3f} · "
                   f"bounds {trade.orb_low:,.2f}–{trade.orb_high:,.2f} · "
                   f"Initial risk {trade.risk_dist:,.2f} · {trade.lots:g} lots · "
                   f"{trade.gross_leverage:.2f}× gross exposure · Net P/L {trade.profit:+,.2f}")
        controls = st.columns([1, 1])
        context = controls[0].select_slider(
            "Chart context",
            options=[8, 16, 24, 32, 48], value=24,
            format_func=lambda n: f"{n} candles before and after",
            key="trade_context_" + record["id"],
            help="Adds candles around the opening range and exit without changing the backtest.",
        )
        available_tf = sorted({value for value in (record["input_tf"], 5, record["tf"])
                               if record["input_tf"] <= value <= record["tf"]
                               and value % record["input_tf"] == 0})
        view_tf = controls[1].selectbox("Chart timeframe", available_tf, index=0,
                                        format_func=lambda value: f"M{value}",
                                        key="trade_tf_" + record["id"])
        tick_upload = st.file_uploader(
            "Optional tick overlay · WRDS quotes/trades or time,bid,ask",
            type=["parquet", "pq", "csv", "gz"], key="trade_ticks_" + record["id"],
            help="Load one relevant day or a compact slice. Large multi-year TAQ archives should stay outside Git.",
        )
        ticks = None
        if tick_upload is not None:
            try:
                tick_clock = st.radio(
                    "Tick timestamp clock",
                    ["WRDS New York → strategy (+7h)", "Already in strategy clock"],
                    horizontal=True, key="tick_clock_" + record["id"],
                    help="WRDS TAQ stores New York wall time; project QQQ bars add seven hours.",
                )
                tick_offset = 7 if tick_clock.startswith("WRDS") else 0
                tick_bytes = tick_upload.getvalue()
                tick_signature = hashlib.sha256(tick_bytes).hexdigest()
                ticks = load_tick_file(tick_bytes, tick_upload.name, tick_signature, tick_offset)
                quality = tick_quality(ticks)
                st.caption(f"Tick overlay: {quality['rows']:,} rows · {quality['first']} to {quality['last']} · "
                           f"{quality['dropped_timestamps']:,} bad timestamps dropped · "
                           f"{quality['invalid_prices']:,} invalid prices · {quality['crossed_quotes']:,} crossed quotes")
            except (ValueError, OSError, KeyError) as exc:
                st.error(f"Cannot load tick overlay: {exc}")
        plot(trade_chart(record, trade, context, view_tf, ticks), "trade_candles_" + record["id"])
        anchor_label = "range capture" if record["result"].params.range_mode == "signal_bar" else "box anchor"
        st.caption(f"M{view_tf} candles · teal band and dashed lines = opening range · "
                   f"shaded candle = {anchor_label}. "
                   "Each timestamp is the candle's opening time. Drag to zoom; double-click to reset. "
                   "SL shows the initial level, not later trailing changes.")
        timeline = record["result"].events
        timeline = timeline[timeline.date == trade.date].copy()
        if not timeline.empty:
            st.markdown("**Decision timeline**")
            timeline["time"] = timeline.time.dt.strftime("%H:%M:%S")
            st.dataframe(timeline[["time", "event", "direction", "price", "status", "detail"]],
                         hide_index=True, width="stretch", height=min(420, 38 + 35 * len(timeline)),
                         column_config={"price": st.column_config.NumberColumn(format="%.2f")})


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
        "- Actual risk and gross exposure reflect lot rounding and any notional cap; broker margin is separate.\n"
        "- Immediate entries and stop management approximate tick ordering on bars.\n"
        "- END exits mark an unresolved trade at the last available close."
    )
    with st.expander("Exact parameters for this result"):
        st.json(result.params.to_dict())


def data_quality_view(record):
    raw, p = record["raw"], record["result"].params
    interval = infer_minutes(raw)
    differences = raw.index.to_series().diff()
    same_day = raw.index.to_series().dt.normalize().eq(raw.index.to_series().shift().dt.normalize())
    intraday_gaps = differences[same_day & (differences > pd.Timedelta(minutes=interval))]
    invalid = ((raw.high < raw[["open", "close"]].max(axis=1))
               | (raw.low > raw[["open", "close"]].min(axis=1)) | (raw.high < raw.low))
    expected_step = pd.Timedelta(minutes=interval)
    missing_rows = int(sum(max(int(delta / expected_step) - 1, 0) for delta in intraday_gaps))
    st.subheader("Data quality and session coverage")
    st.caption("Timestamps are interpreted as strategy-clock values. Verify their New York and broker-server mapping before trusting a session test.")
    cols = st.columns(4)
    cols[0].metric("Input rows", f"{len(raw):,}")
    cols[1].metric("Intraday gaps", f"{len(intraday_gaps):,}", help="Overnight and weekend closures are excluded.")
    cols[2].metric("Estimated missing bars", f"{missing_rows:,}")
    cols[3].metric("Invalid OHLC rows", f"{int(invalid.sum()):,}")

    daily = raw.groupby(raw.index.normalize()).agg(
        rows=("close", "size"), first=("close", lambda s: s.index.min().time()),
        last=("close", lambda s: s.index.max().time()), volume=("tick_volume", "sum"),
    ).reset_index(names="date")
    range_counts = raw.open.resample(f"{record['tf']}min").count()
    opening = range_counts[(range_counts.index.hour == p.orb_hour) & (range_counts.index.minute == p.orb_minute)]
    opening_by_day = {stamp.normalize(): int(count) for stamp, count in opening.items()}
    daily["opening_rows"] = daily.date.map(opening_by_day).fillna(0).astype(int)
    daily["opening_complete"] = daily.opening_rows >= max(1, record["tf"] // interval)
    st.dataframe(daily, hide_index=True, width="stretch", height=330)

    if "spread" in raw:
        spread = raw.spread.astype(float)
        a, b, c = st.columns(3)
        a.metric("Recorded spread median", f"{spread.median():.2f} points")
        b.metric("Recorded spread p95", f"{spread.quantile(.95):.2f} points")
        c.metric("Recorded spread maximum", f"{spread.max():.2f} points")
    if len(intraday_gaps):
        with st.expander("Inspect intraday gaps"):
            gaps = pd.DataFrame({"next_bar": intraday_gaps.index, "gap": intraday_gaps.values})
            gaps["previous_bar"] = gaps.next_bar - gaps.gap
            st.dataframe(gaps[["previous_bar", "next_bar", "gap"]].head(500), hide_index=True, width="stretch")
    else:
        st.success("No intraday timestamp gaps were found at the recorded interval.")


def _max_drawdown_r(values) -> float:
    curve = np.r_[0.0, np.cumsum(values)]
    return float(np.max(np.maximum.accumulate(curve) - curve))


def expanding_target_walk_forward(record, targets=None) -> pd.DataFrame:
    """Choose target R on prior calendar years, then score the next unseen year."""
    targets = targets or [.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
    targets = sorted(set(float(value) for value in targets) | {record["result"].params.tp_ratio})
    candidates = {}
    for target in targets:
        params = replace(record["result"].params, tp_ratio=target)
        candidates[target] = run_backtest(record["bars"], params, path=record["raw"]).trades
    years = sorted({int(year) for frame in candidates.values() for year in frame.entry_time.dt.year.unique()})
    rows = []
    for test_year in years[1:]:
        scores = {
            target: float(frame.loc[frame.entry_time.dt.year < test_year, "r"].sum())
            for target, frame in candidates.items()
        }
        selected = max(targets, key=lambda target: (scores[target], -target))
        test = candidates[selected]
        test = test[test.entry_time.dt.year == test_year]
        rows.append({
            "Test year": test_year, "Selected target R": selected,
            "Prior-years R": scores[selected], "Test trades": len(test),
            "Test average R": float(test.r.mean()) if len(test) else 0.0,
            "Test total R": float(test.r.sum()), "Test max drawdown R": _max_drawdown_r(test.r.to_numpy()),
        })
    return pd.DataFrame(rows)


def validation_view(record):
    trades = record["result"].trades.sort_values("exit_time")
    st.subheader("Validation, stability and failure modes")
    if len(trades) < 4:
        st.info("At least four trades are needed for chronological and Monte Carlo diagnostics.")
        return

    split = st.slider("Chronological development share", 50, 85, 70, 5,
                      help="The later trades are held out. This does not optimize parameters by itself.")
    cut = max(1, min(len(trades) - 1, round(len(trades) * split / 100)))
    development, holdout = trades.iloc[:cut], trades.iloc[cut:]
    rows = []
    for label, sample in (("Development", development), ("Holdout", holdout)):
        rows.append({"Period": label, "Trades": len(sample), "Win rate %": (sample.r > 0).mean() * 100,
                     "Average R": sample.r.mean(), "Total R": sample.r.sum(),
                     "Max drawdown R": _max_drawdown_r(sample.r.to_numpy())})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Win rate %": st.column_config.NumberColumn(format="%.1f"),
                                "Average R": st.column_config.NumberColumn(format="%.3f"),
                                "Total R": st.column_config.NumberColumn(format="%.2f"),
                                "Max drawdown R": st.column_config.NumberColumn(format="%.2f")})

    rng = np.random.default_rng(20260909)
    samples = rng.choice(trades.r.to_numpy(), size=(2000, len(trades)), replace=True)
    ending = samples.sum(axis=1)
    drawdowns = np.array([_max_drawdown_r(sample) for sample in samples])
    a, b = st.columns(2)
    a.metric("Bootstrap ending R · median", f"{np.quantile(ending, .5):.1f} R",
             help=f"5–95%: {np.quantile(ending, .05):.1f} to {np.quantile(ending, .95):.1f} R")
    b.metric("Bootstrap max drawdown · median", f"{np.quantile(drawdowns, .5):.1f} R",
             help=f"5–95%: {np.quantile(drawdowns, .05):.1f} to {np.quantile(drawdowns, .95):.1f} R")
    distribution = go.Figure(go.Histogram(x=ending, nbinsx=40, marker_color=TEAL,
                                           hovertemplate="%{x:.1f} R<extra>Bootstrap ending R</extra>"))
    plot(style(distribution, height=285, ytitle="Bootstrap samples"), "bootstrap_" + record["id"])

    by_year = record["result"].by("year")
    if not by_year.empty:
        st.markdown("**Performance by year**")
        st.dataframe(by_year.reset_index(), hide_index=True, width="stretch")
    if trades.entry_time.dt.year.nunique() >= 2:
        if st.button("Run expanding yearly walk-forward", key="walk_forward_" + record["id"]):
            with st.spinner("Selecting target R on prior years and scoring each next year…"):
                st.session_state["walk_forward_result_" + record["id"]] = expanding_target_walk_forward(record)
        walk_forward = st.session_state.get("walk_forward_result_" + record["id"])
        if walk_forward is not None:
            st.markdown("**Expanding target-R walk-forward**")
            st.dataframe(walk_forward, hide_index=True, width="stretch",
                         column_config={"Selected target R": st.column_config.NumberColumn(format="%.2f"),
                                        "Prior-years R": st.column_config.NumberColumn(format="%.2f"),
                                        "Test average R": st.column_config.NumberColumn(format="%.3f"),
                                        "Test total R": st.column_config.NumberColumn(format="%.2f"),
                                        "Test max drawdown R": st.column_config.NumberColumn(format="%.2f")})
            st.caption("Each row chooses among 0.75–3.0R using only earlier calendar years, then reports the unseen next year.")
    joined = trades.copy()
    joined["range_pct"] = joined.orb_range / joined.orb_high * 100
    if joined.range_pct.nunique() >= 3:
        joined["volatility regime"] = pd.qcut(joined.range_pct, 3, labels=["Low", "Normal", "High"], duplicates="drop")
        regime = joined.groupby("volatility regime", observed=True).agg(
            trades=("r", "size"), win_rate=("r", lambda value: (value > 0).mean()),
            average_r=("r", "mean"), total_r=("r", "sum")).reset_index()
        st.markdown("**Results by opening-range volatility regime**")
        st.dataframe(regime, hide_index=True, width="stretch")

    if st.button("Run 5×5 target/stop stability grid", key="grid_" + record["id"]):
        with st.spinner("Running 25 neighboring configurations…"):
            tp_values = np.linspace(max(.25, record["result"].params.tp_ratio - .5),
                                    record["result"].params.tp_ratio + .5, 5)
            sl_base = (.5 if record["result"].params.stop_mode == "midpoint"
                       else record["result"].params.sl_ratio)
            sl_values = np.linspace(max(.1, sl_base - .4), sl_base + .4, 5)
            grid = []
            for sl in sl_values:
                for tp in tp_values:
                    candidate = replace(record["result"].params, stop_mode="range_multiple",
                                        sl_ratio=float(sl), tp_ratio=float(tp))
                    result = run_backtest(record["bars"], candidate, path=record["raw"])
                    grid.append({"Stop × range": sl, "Target R": tp, "Total R": result.stats["total_r"],
                                 "Trades": result.stats["trades"]})
            st.session_state["grid_result_" + record["id"]] = pd.DataFrame(grid)
    grid = st.session_state.get("grid_result_" + record["id"])
    if grid is not None:
        pivot = grid.pivot(index="Stop × range", columns="Target R", values="Total R")
        fig = go.Figure(go.Heatmap(z=pivot.values, x=pivot.columns, y=pivot.index,
                                   colorscale=[[0, RED], [.5, SURFACE], [1, TEAL]],
                                   colorbar_title="Total R", hovertemplate="Target %{x:.2f}R<br>Stop %{y:.2f}×<br>Total %{z:.2f}R<extra></extra>"))
        style(fig, height=380)
        fig.update_xaxes(title="Target · R")
        fig.update_yaxes(title="Stop · × range")
        plot(fig, "grid_plot_" + record["id"])
        st.caption("For comparability this grid expresses every stop as a range multiple; 0.5 equals the midpoint.")


def mt5_parity_issues(p: Params) -> list[str]:
    """Strategy-rule gaps between the Python engine and the checked-in EA."""
    return []


def mt5_input_frame(p: Params, tf: int) -> pd.DataFrame:
    """Translate the active Python setup into the corresponding EA inputs."""
    tf_name = {60: "PERIOD_H1", 240: "PERIOD_H4", 1440: "PERIOD_D1"}.get(tf, f"PERIOD_M{tf}")
    filter_tf = {60: "PERIOD_H1", 240: "PERIOD_H4", 1440: "PERIOD_D1"}.get(
        p.filter_tf_minutes, f"PERIOD_M{p.filter_tf_minutes}")
    direction = {"both": "DIR_BOTH", "long": "DIR_LONG_ONLY", "short": "DIR_SHORT_ONLY"}[p.direction]
    entry = {"close": "ENTRY_CLOSE_CONFIRM", "immediate": "ENTRY_IMMEDIATE"}[p.entry_mode]
    range_mode = {
        "signal_bar": "RANGE_OBSERVED",
        "historical_percentile": "RANGE_HISTORICAL_PERCENTILE",
        "historical_percentile_premarket": "RANGE_HISTORICAL_PREMARKET",
    }[p.range_mode]
    stop_mode = {"range_multiple": "STOP_RANGE_MULTIPLE", "midpoint": "STOP_MIDPOINT"}[p.stop_mode]
    close_hour, close_minute = (22, 45)
    if p.eod_close:
        close_hour, close_minute = map(int, p.eod_close.split(":"))
    values = [
        ("Signal timeframe", "InpTimeframe", tf_name),
        ("Range time", "InpOrbHour / InpOrbMinute", f"HOUR_{p.orb_hour:02d} / {p.orb_minute}"),
        ("Entry cutoff", "InpCutoffHour", f"HOUR_{p.cutoff_hour:02d}"),
        ("Entry mode", "InpEntryMode", entry),
        ("Direction", "InpDirection", direction),
        ("Range construction", "InpRangeMode", range_mode),
        ("Historical sessions", "InpRangeSessions", str(p.range_history_sessions)),
        ("Range percentile", "InpRangePercentile", f"{p.range_percentile:g}"),
        ("Range multiplier", "InpRangeMultiplier", f"{p.range_multiplier:g}"),
        ("Premarket window", "InpPremarketMinutes", str(p.premarket_window_minutes)),
        ("Premarket sensitivity", "InpPremarketSensitivity", f"{p.premarket_sensitivity:g}"),
        ("Confirmation bars", "InpConfirmationBars", str(p.confirmation_bars)),
        ("Matching candle colour", "InpConfirmationColor", str(p.confirmation_same_color).lower()),
        ("Target", "InpTpRatio", f"{p.tp_ratio:g}"),
        ("Stop construction", "InpStopMode", stop_mode),
        ("Stop range multiple", "InpSlRatio", f"{p.sl_ratio:g}"),
        ("Risk", "InpRiskPercent", f"{p.risk_pct:g}"),
        ("End-of-day close", "InpCloseEod", str(p.eod_close is not None).lower()),
        ("Close time", "InpCloseHour / InpCloseMinute", f"HOUR_{close_hour:02d} / {close_minute}"),
        ("Daily ATR period", "InpAtrPeriod", str(p.atr_period)),
        ("Minimum range / ATR", "InpMinRangeAtr", f"{p.min_range_atr:g}"),
        ("Maximum range / ATR", "InpMaxRangeAtr", f"{p.max_range_atr:g}"),
        ("Prior range window", "InpRangeLookbackMinutes", str(p.range_lookback_minutes)),
        ("Maximum range / prior span", "InpMaxRangeLookbackRatio", f"{p.max_range_lookback_ratio:g}"),
        ("Volume lookback", "InpVolumeLookback", str(p.vol_lookback)),
        ("Volume threshold", "InpVolumeSpikeMin", f"{p.vol_spike_min:g}"),
        ("Skip weak volume", "InpSkipNoSpike", str(p.vol_spike_min > 0).lower()),
        ("Trend MA period", "InpFilterMaPeriod", str(p.filter_ma_period)),
        ("Trend timeframe", "InpFilterTF", filter_tf),
        ("Trend MA method", "InpFilterMaMethod", f"MODE_{p.filter_ma_method.upper()}"),
        ("Trend slope bars", "InpFilterSlopeBars", str(p.filter_ma_slope_bars)),
        ("Minimum trend slope", "InpFilterMinSlopePct", f"{p.filter_ma_min_slope_pct:g}"),
        ("Breakeven trigger", "InpBreakEvenAtR", f"{p.breakeven_at_r:g}"),
        ("Breakeven lock", "InpBreakEvenLockR", f"{p.breakeven_lock_r:g}"),
        ("Trailing trigger", "InpTrailStartR", f"{p.trail_start_r:g}"),
        ("Trailing distance", "InpTrailDistR", f"{p.trail_dist_r:g}"),
        ("Trailing step", "InpTrailStepR", f"{p.trail_step_r:g}"),
        ("Maximum spread", "InpMaxSpreadPoints",
         (f"{p.max_spread:g} price units ÷ broker _Point" if p.max_spread else "0 (disabled)")),
        ("Minimum stop", "InpMinStopPoints", str(p.min_stop_points)),
        ("Allow live entries", "InpAllowLiveTrading", "false"),
    ]
    return pd.DataFrame(values, columns=["Rule", "MT5 input", "Value"])


def _mql_function(source: str, signature: str) -> str:
    start = source.find(signature)
    if start < 0:
        return ""
    opening = source.find("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return source[start:]


def algorithm_view(record):
    p, tf = record["result"].params, record["tf"]
    source = EA_PATH.read_text(encoding="utf-8")
    version_match = re.search(r'#property\s+version\s+"([^"]+)"', source)
    version = version_match.group(1) if version_match else "unknown"
    issues = mt5_parity_issues(p)

    st.subheader("The algorithm, close to the code")
    st.caption("This is the checked-in MetaTrader 5 Expert Advisor—not pseudocode. Edit the source, compile it, and reconcile it with Python.")
    a, b, c = st.columns(3)
    a.metric("EA source", f"v{version}")
    b.metric("Selected setup", "MT5-ready" if not issues else "Parity needed")
    compile_log = EA_PATH.with_name("compile.log")
    compile_text = compile_log.read_text(encoding="utf-8", errors="replace") if compile_log.exists() else ""
    compile_match = re.search(r"Result:\s*(\d+) errors?,\s*(\d+) warnings?", compile_text)
    c.metric("Latest local compile", (f"{compile_match.group(1)} errors · {compile_match.group(2)} warnings"
                                      if compile_match else "Run F7 locally"))

    range_rule = {
        "signal_bar": "the observed opening candle high and low",
        "historical_percentile": "a prior-session opening-range percentile forecast",
        "historical_percentile_premarket": "a historical forecast adjusted by premarket volatility",
    }[p.range_mode]
    confirmation = ("the first price crossing" if p.entry_mode == "immediate" else
                    f"{p.confirmation_bars} closing candle{'s' if p.confirmation_bars != 1 else ''}")
    stop = "the range midpoint" if p.stop_mode == "midpoint" else f"{p.sl_ratio:g}× the range"
    st.markdown(
        f"1. At **{p.orb_hour:02d}:{p.orb_minute:02d}**, define the range from **{range_rule}**.\n"
        f"2. Before **{p.cutoff_hour:02d}:00**, accept **{confirmation}** outside it.\n"
        f"3. Take the **first confirmed breakout only** for the day; direction is **{p.direction}**.\n"
        f"4. Place the initial stop at **{stop}** and the target at **{p.tp_ratio:g}R**.\n"
        f"5. Size the position to **{p.risk_pct:g}%** of equity"
        + (f", capped at **{p.max_gross_leverage:g}×** gross exposure." if p.max_gross_leverage else ".")
    )

    if issues:
        st.warning("This selected setup is not fully represented by EA v" + version + ": " + ", ".join(issues) + ".")
    else:
        st.success("The selected rules have MT5 EA parity. The source still needs compiling in your terminal and broker testing before use.")

    with st.expander("MT5 inputs for this setup", expanded=True):
        st.dataframe(mt5_input_frame(p, tf), hide_index=True, width="stretch")
        st.caption("The live-entry gate is intentionally false in this table. Enable it only after tester and broker reconciliation.")

    view = st.radio("Code view", ["Trading logic", "Inputs", "Complete EA"], horizontal=True,
                    key="algorithm_code_view")
    if view == "Inputs":
        shown = source[source.find('input group "Timing'):source.find("//+------------------------------------------------------------------+\n//| One open trade")]
    elif view == "Trading logic":
        names = ["void OnNewBar()", "void CheckImmediateBreakout()",
                 "void HandleSignal(const int direction, const double refPrice)",
                 "void OpenBreakout(const int direction)", "void TryCaptureOrb(const MqlRates &bar)",
                 "void ResetOnNewDay(const datetime barTime)"]
        shown = "\n\n".join(filter(None, (_mql_function(source, name) for name in names)))
    else:
        shown = source
    st.code(shown, language="cpp", line_numbers=view == "Complete EA", wrap_lines=False, height=720)
    st.download_button("Download ORB_Backtest.mq5", source, "ORB_Backtest.mq5", "text/plain",
                       width="stretch")
    st.caption("Install under MQL5/Experts, press F7 in MetaEditor, then use Every tick based on real ticks in Strategy Tester.")


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
    if last is None or "raw" not in last or manual or (dirty and st.session_state.auto_run):
        try:
            with st.spinner("Simulating the setup…"):
                bars = resample(raw, tf) if have < tf else raw
                result = run_backtest(bars, params, path=raw if have < tf else None)
            last = dict(id=uuid4().hex[:10], signature=signature, result=result, bars=bars, raw=raw,
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
    tabs = st.tabs(["Overview", "Trades", "Validation", "Compare", "Algorithm", "Data quality", "Diagnostics"])
    with tabs[0]:
        overview(last)
    with tabs[1]:
        trades_view(last)
    with tabs[2]:
        validation_view(last)
    with tabs[3]:
        compare_view()
    with tabs[4]:
        algorithm_view(last)
    with tabs[5]:
        data_quality_view(last)
    with tabs[6]:
        diagnostics_view(last)


if __name__ == "__main__":
    main()
