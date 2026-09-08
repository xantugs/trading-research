"""ORB Backtester - a point-and-click front end for the Python engine.

    streamlit run app.py

Pick a data file, choose a preset or move the sliders, press Run.  Every run is
added to the comparison table at the bottom so presets can be judged side by
side.  All times are broker server time, exactly like the MetaTrader EA.
"""
from __future__ import annotations

import io
import os
from dataclasses import replace
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from orb import Params, load_bars, infer_minutes, resample, run_backtest
from orb.config import load_params

# --------------------------------------------------------------------------- #
# palette (validated: blue/red polarity pair passes CVD + contrast on #fcfcfb)
# --------------------------------------------------------------------------- #
BLUE, RED = "#2a78d6", "#e34948"
BLUE_SOFT, RED_SOFT = "rgba(42,120,214,0.15)", "rgba(227,73,72,0.18)"
SURFACE, INK, INK2, MUTED, GRID, BASELINE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

DATA_DIR = Path(__file__).parent / "data"

PRESETS = {
    "Baseline (the video's settings)": dict(),
    "Tight stop: 0.5 x range": dict(sl_ratio=0.5),
    "Wider target: 2R": dict(tp_ratio=2.0),
    "Immediate entry (no close confirmation)": dict(entry_mode="immediate"),
    "Long only + H1 EMA(50) trend filter": dict(direction="long", filter_ma_period=50, filter_tf_minutes=60),
    "Flat at 22:45 + breakeven at 1R": dict(eod_close="22:45", breakeven_at_r=1.0),
    "Trailing 1R behind price from +1R": dict(tp_ratio=5.0, trail_start_r=1.0, trail_dist_r=1.0),
    "Skip huge ranges (> 0.5 x daily ATR)": dict(max_range_atr=0.5),
}


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Loading bars...")
def load_file(path_or_bytes, name: str) -> pd.DataFrame:
    if isinstance(path_or_bytes, bytes):
        return load_bars(path_or_bytes)
    return load_bars(path_or_bytes)


@st.cache_data(show_spinner=False)
def to_timeframe(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    return resample(bars, minutes)


# --------------------------------------------------------------------------- #
# charts
# --------------------------------------------------------------------------- #
def style(fig: go.Figure, height: int = 320, ytitle: str = "", xtitle: str = "") -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, color=INK2, size=12),
        showlegend=False,
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#ffffff", font=dict(family=FONT, color=INK)),
        bargap=0.35,
    )
    fig.update_xaxes(showgrid=False, linecolor=BASELINE, tickcolor=BASELINE, tickfont=dict(color=MUTED), title=xtitle)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=True, zerolinecolor=BASELINE, zerolinewidth=1,
                     linecolor=SURFACE, tickfont=dict(color=MUTED), title=ytitle)
    return fig


def equity_chart(equity: pd.Series, initial: float) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=equity.index, y=equity.values, mode="lines", line=dict(color=BLUE, width=2),
                             name="Balance", hovertemplate="%{y:,.0f}<extra></extra>"))
    fig.add_hline(y=initial, line=dict(color=BASELINE, width=1))
    return style(fig, height=340, ytitle="Balance")


def drawdown_chart(equity: pd.Series) -> go.Figure:
    dd = (equity / equity.cummax() - 1.0) * 100.0
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, mode="lines", line=dict(color=RED, width=2), fill="tozeroy",
                             fillcolor=RED_SOFT, name="Drawdown", hovertemplate="%{y:.1f}%<extra></extra>"))
    return style(fig, height=180, ytitle="Drawdown %")


def signed_bars(x, y, hover_extra=None, height=260, ytitle="R") -> go.Figure:
    colors = [BLUE if v >= 0 else RED for v in y]
    custom = hover_extra if hover_extra is not None else [""] * len(y)
    fig = go.Figure(go.Bar(x=x, y=y, marker_color=colors, customdata=custom,
                           hovertemplate="%{y:.2f} R %{customdata}<extra></extra>"))
    fig.update_traces(marker_line_width=0)
    return style(fig, height=height, ytitle=ytitle)


def histogram(r: pd.Series) -> go.Figure:
    fig = go.Figure(go.Histogram(x=r, xbins=dict(size=0.25), marker_color=BLUE, marker_line_width=0,
                                 hovertemplate="%{x}: %{y} trades<extra></extra>"))
    fig.update_layout(bargap=0.08)
    return style(fig, height=260, ytitle="Trades", xtitle="R multiple")


# --------------------------------------------------------------------------- #
# sidebar
# --------------------------------------------------------------------------- #
def sidebar() -> tuple:
    st.sidebar.title("ORB Backtester")
    st.sidebar.caption("Times use the dataset's strategy clock. Match this clock to your MetaTrader server before comparing results.")

    files = sorted(str(p.relative_to(DATA_DIR)) for p in DATA_DIR.rglob("*_M1.csv"))
    selected = os.environ.get("ORB_DATA_FILE")
    if selected and Path(selected).exists():
        selected = str(Path(selected).resolve())
        if selected not in files:
            files.insert(0, selected)
    choice = st.sidebar.selectbox("Data file (in the data folder)", files, index=0 if files else None)
    upload = st.sidebar.file_uploader("...or upload a bars CSV", type=["csv", "txt"])

    if "cfg" not in st.session_state:
        config_path = os.environ.get("ORB_PARAMS_FILE")
        st.session_state.cfg = load_params(config_path) if config_path else Params(spread=1.0)
    preset = st.sidebar.selectbox("Preset", list(PRESETS))
    if st.sidebar.button("Apply preset"):
        st.session_state.cfg = replace(Params(spread=st.session_state.cfg.spread,
                                                 point=st.session_state.cfg.point,
                                                 point_value=st.session_state.cfg.point_value,
                                                 initial_balance=st.session_state.cfg.initial_balance),
                                          **PRESETS[preset])
        st.rerun()
    p: Params = st.session_state.cfg

    with st.sidebar.form("param_form"):
        st.subheader("Timing")
        tf = st.selectbox("Signal timeframe (minutes)", [5, 15, 30, 60], index=[5, 15, 30, 60].index(int(os.environ.get("ORB_SIGNAL_TF", "15"))))
        c1, c2 = st.columns(2)
        orb_hour = c1.number_input("Range candle hour", 0, 23, p.orb_hour)
        orb_minute = c2.number_input("Range candle minute", 0, 59, p.orb_minute, step=5)
        cutoff = st.number_input("No new entries from hour", 1, 23, p.cutoff_hour)

        st.subheader("Strategy")
        entry_mode = st.radio("Entry", ["close", "immediate"], index=0 if p.entry_mode == "close" else 1,
                              format_func=lambda v: "wait for a close outside the range" if v == "close" else "enter the moment price crosses",
                              horizontal=False)
        direction = st.radio("Direction", ["both", "long", "short"], index=["both", "long", "short"].index(p.direction), horizontal=True)
        tp = st.slider("Take profit (x risk)", 0.5, 5.0, float(p.tp_ratio), 0.1)
        sl = st.slider("Stop loss (x range)", 0.25, 2.0, float(p.sl_ratio), 0.05)
        risk = st.slider("Risk per trade (% equity)", 0.25, 5.0, float(p.risk_pct), 0.25)

        st.subheader("End of day")
        eod_on = st.checkbox("Close everything at end of session", value=p.eod_close is not None)
        eod_time = st.text_input("Close time (HH:MM)", p.eod_close or "22:45")

        st.subheader("Opening-range filters")
        atr_period = st.number_input("ATR lookback (completed days)", 0, 200, int(p.atr_period))
        vol_lookback = st.number_input("Volume lookback (signal bars)", 0, 200, int(p.vol_lookback))
        c1, c2 = st.columns(2)
        min_atr = c1.number_input("Min range / ATR (0 = off)", 0.0, 5.0, float(p.min_range_atr), 0.05)
        max_atr = c2.number_input("Max range / ATR (0 = off)", 0.0, 5.0, float(p.max_range_atr), 0.05)
        vol_min = st.number_input("Require volume spike of at least x (0 = off)", 0.0, 20.0, float(p.vol_spike_min), 0.5)

        st.subheader("Trend filter")
        ma_period = st.number_input("MA period (0 = off)", 0, 500, int(p.filter_ma_period))
        c1, c2 = st.columns(2)
        ma_tf = c1.selectbox("MA timeframe (min)", [15, 30, 60, 240, 1440], index=[15, 30, 60, 240, 1440].index(p.filter_tf_minutes) if p.filter_tf_minutes in [15, 30, 60, 240, 1440] else 2)
        ma_method = c2.selectbox("MA type", ["ema", "sma"], index=0 if p.filter_ma_method == "ema" else 1)

        st.subheader("Exit management (R = initial risk)")
        c1, c2 = st.columns(2)
        be_at = c1.number_input("Breakeven at +R (0 = off)", 0.0, 5.0, float(p.breakeven_at_r), 0.1)
        be_lock = c2.number_input("...locking R", 0.0, 5.0, float(p.breakeven_lock_r), 0.1)
        c1, c2, c3 = st.columns(3)
        tr_start = c1.number_input("Trail from +R (0 = off)", 0.0, 5.0, float(p.trail_start_r), 0.1)
        tr_dist = c2.number_input("Trail distance R", 0.1, 5.0, float(p.trail_dist_r), 0.1)
        tr_step = c3.number_input("Trail step R", 0.0, 2.0, float(p.trail_step_r), 0.05)

        st.subheader("Execution & money")
        spread = st.number_input("Spread (price units, ask - bid)", 0.0, 50.0, float(p.spread), 0.1)
        use_data_spread = st.checkbox("Use the spread column of the data instead", value=p.use_data_spread)
        same_bar = st.selectbox("Bar touches stop AND target", ["sl_first", "tp_first"],
                                index=["sl_first", "tp_first"].index(p.same_bar_rule),
                                format_func=lambda v: "count it as a loss (conservative)" if v == "sl_first" else "count it as a win (optimistic)")
        c1, c2 = st.columns(2)
        balance = c1.number_input("Starting balance", 100.0, 10_000_000.0, float(p.initial_balance), 100.0)
        point_value = c2.number_input("Money per 1.0 price move per lot", 0.01, 10_000.0, float(p.point_value), 0.01)
        point = st.number_input("Symbol point (0.01 for a 2-digit index CFD)", 0.0001, 1.0, float(p.point), format="%.4f")
        commission = st.number_input("Commission per lot per side", 0.0, 100.0, float(p.commission_per_lot), 0.5)
        lot_step = st.number_input("Lot step", min_value=0.001, value=float(p.lot_step), format="%.3f")
        min_lot = st.number_input("Minimum lot", min_value=0.001, value=float(p.min_lot), format="%.3f")
        max_lot = st.number_input("Maximum lot", min_value=0.001, value=float(p.max_lot), format="%.3f")

        run = st.form_submit_button("Run backtest", type="primary", use_container_width=True)

    params = Params(
        orb_hour=int(orb_hour), orb_minute=int(orb_minute), cutoff_hour=int(cutoff),
        entry_mode=entry_mode, direction=direction, tp_ratio=tp, sl_ratio=sl, risk_pct=risk,
        eod_close=eod_time if eod_on else None,
        min_range_atr=min_atr, max_range_atr=max_atr, vol_spike_min=vol_min,
        atr_period=int(atr_period), vol_lookback=int(vol_lookback),
        filter_ma_period=int(ma_period), filter_tf_minutes=int(ma_tf), filter_ma_method=ma_method,
        breakeven_at_r=be_at, breakeven_lock_r=be_lock, trail_start_r=tr_start, trail_dist_r=tr_dist, trail_step_r=tr_step,
        spread=spread, use_data_spread=use_data_spread, point=point, same_bar_rule=same_bar,
        initial_balance=balance, point_value=point_value, commission_per_lot=commission,
        lot_step=lot_step, min_lot=min_lot, max_lot=max_lot,
    )
    st.session_state.cfg = params
    return choice, upload, int(tf), params, run


# --------------------------------------------------------------------------- #
# main page
# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(page_title="ORB Backtester", layout="wide")
    choice, upload, tf, params, run = sidebar()

    try:
        if upload is not None:
            raw = load_file(upload.getvalue(), upload.name)
            source = upload.name
        elif choice:
            raw = load_file(str(DATA_DIR / choice), choice)
            source = Path(choice).name
        else:
            st.info("Run python run_all.py to prepare demo data, or upload a bars CSV.")
            return
        have = infer_minutes(raw)
    except (ValueError, OSError, KeyError) as exc:
        st.error(f"Could not load bars: {exc}")
        return
    if have > tf:
        st.error(f"{source} is {have}-minute data; it cannot be turned into {tf}-minute bars. Export a finer timeframe.")
        return
    path = raw if have < tf else None
    bars = to_timeframe(raw, tf) if have < tf else raw

    st.title("Opening Range Breakout - backtest")
    st.caption(
        f"**{source}** - {len(raw):,} bars of {have}-minute data, {raw.index[0]:%Y-%m-%d} to {raw.index[-1]:%Y-%m-%d}"
        + (f"; exits resolved on the {have}-minute path" if path is not None else "; exits resolved on the signal bars (M1 data is better)")
        + (". *Synthetic random-walk data - expect noise around zero.*" if "SYNTHETIC" in source.upper() else "")
    )

    if "runs" not in st.session_state:
        st.session_state.runs = []

    if not run and "last" not in st.session_state:
        st.info("Set the parameters in the sidebar and press **Run backtest**.")
        return

    if run:
        try:
            with st.spinner("Running..."):
                res = run_backtest(bars, params, path=path)
        except ValueError as e:
            st.error(str(e))
            return
        st.session_state.last = res
        st.session_state.last_source = source
        s = res.stats
        st.session_state.runs.append({
            "run": len(st.session_state.runs) + 1,
            "source": source,
            "entry": params.entry_mode, "dir": params.direction,
            "TP": f"{params.tp_ratio:g}", "SL": f"{params.sl_ratio:g}",
            "EOD": params.eod_close or "off", "MA": f"{params.filter_ma_period}" if params.filter_ma_period else "off",
            "BE": f"{params.breakeven_at_r:g}" if params.breakeven_at_r else "off",
            "trail": f"{params.trail_start_r:g}" if params.trail_start_r else "off",
            "range/ATR max": f"{params.max_range_atr:g}" if params.max_range_atr else "off",
            "trades": int(s["trades"]), "win %": round(100 * s["win_rate"], 1), "avg R": round(s["avg_r"], 3),
            "total R": round(s["total_r"], 1),
            "PF": round(s["profit_factor"], 2) if s["profit_factor"] != float("inf") else float("nan"),
            "max DD %": round(s["max_dd_pct"], 1), "return %": round(s["return_pct"], 1),
        })

    res = st.session_state.last
    params = res.params
    st.caption(f"Results below: {st.session_state.get('last_source', 'previous data')} · "
               "Press Run backtest to apply changes. R is before costs; money profit includes commission. "
               "Drawdown uses closed-trade balance. Minimum lot sizing can exceed the requested risk.")
    s = res.stats
    t = res.trades

    # ---- KPI row -----------------------------------------------------------
    k = st.columns(6)
    k[0].metric("Trades", f"{s['trades']}")
    k[1].metric("Win rate", f"{100 * s['win_rate']:.1f}%")
    k[2].metric("Expectancy", f"{s['avg_r']:+.3f} R")
    k[3].metric("Profit factor", "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}")
    k[4].metric("Max drawdown", f"{s['max_dd_pct']:.1f}%")
    k[5].metric("Return", f"{s['return_pct']:+.1f}%", help=f"Final balance {s['final_balance']:,.0f}")

    if t.empty:
        st.warning("No trades with these settings.")
        return

    # ---- equity ------------------------------------------------------------
    st.subheader("Balance")
    st.plotly_chart(equity_chart(res.equity, params.initial_balance), use_container_width=True)
    st.plotly_chart(drawdown_chart(res.equity), use_container_width=True)
    with st.expander("Table: balance after each trade"):
        st.dataframe(res.equity.rename("balance").to_frame(), use_container_width=True, height=240)

    # ---- monthly R ---------------------------------------------------------
    st.subheader("R per month")
    monthly = t.set_index("exit_time")["r"].resample("MS").agg(["sum", "size"])
    st.plotly_chart(signed_bars(monthly.index.strftime("%Y-%m"), monthly["sum"].values,
                                hover_extra=[f"({n} trades)" for n in monthly["size"]]), use_container_width=True)
    with st.expander("Table: monthly"):
        st.dataframe(monthly.rename(columns={"sum": "total_r", "size": "trades"}), use_container_width=True)

    # ---- distributions -----------------------------------------------------
    c1, c2, c3 = st.columns(3)
    c1.subheader("R distribution")
    c1.plotly_chart(histogram(t["r"]), use_container_width=True)
    by_hour = res.by("entry_hour")
    c2.subheader("Average R by entry hour")
    c2.plotly_chart(signed_bars([f"{h:02d}:00" for h in by_hour.index], by_hour["avg_r"].values,
                                hover_extra=[f"({n} trades)" for n in by_hour["trades"]], ytitle="avg R"), use_container_width=True)
    by_wd = res.by("weekday")
    c3.subheader("Average R by weekday")
    c3.plotly_chart(signed_bars(by_wd.index, by_wd["avg_r"].values,
                                hover_extra=[f"({n} trades)" for n in by_wd["trades"]], ytitle="avg R"), use_container_width=True)
    with st.expander("Tables: by year / hour / weekday / exit reason / direction"):
        cols = st.columns(2)
        cols[0].dataframe(res.by("year"), use_container_width=True)
        cols[1].dataframe(res.by("reason"), use_container_width=True)
        cols = st.columns(3)
        cols[0].dataframe(by_hour, use_container_width=True)
        cols[1].dataframe(by_wd, use_container_width=True)
        cols[2].dataframe(res.by("direction"), use_container_width=True)

    # ---- days & warnings ---------------------------------------------------
    d = res.days
    quiet = d[(d["vol_ratio"] > 0) & (d["vol_ratio"] < 1.5)]
    if len(quiet):
        st.warning(
            f"{len(quiet)} of {len(d)} range candles had no volume spike (less than 1.5x the candles before them). "
            "If they cluster in one part of the year, the broker clock probably does not follow US daylight saving "
            f"and the range candle is not the session open on those dates. First few: {', '.join(str(x) for x in quiet['date'].head(5))}"
        )
    st.subheader("Trades")
    show = t[["date", "direction", "entry_time", "entry", "sl", "tp", "exit_time", "exit", "reason", "r", "lots", "profit", "balance",
              "orb_range", "range_atr", "vol_ratio"]]
    st.dataframe(show, use_container_width=True, height=320)
    buf = io.StringIO()
    show.to_csv(buf, index=False)
    st.download_button("Download trades CSV", buf.getvalue(), file_name="orb_python_trades.csv", mime="text/csv")
    with st.expander("Days: range, filters and what happened"):
        st.dataframe(d, use_container_width=True, height=300)

    # ---- comparison of runs -------------------------------------------------
    if st.session_state.runs:
        st.subheader("Runs this session")
        st.dataframe(pd.DataFrame(st.session_state.runs).set_index("run"), use_container_width=True)
        if st.button("Clear run history"):
            st.session_state.runs = []
            st.rerun()


if __name__ == "__main__":
    main()
