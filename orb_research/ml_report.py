"""Human-readable schema and descriptive checks; no model fitting or feature selection."""
from __future__ import annotations

import pandas as pd

from .ml_dataset import EXECUTION_COLUMNS, IDENTIFIER_COLUMNS, LABEL_COLUMNS
from .ml_features import FEATURE_COLUMNS


FEATURE_DEFINITIONS = {
    "direction_sign": ("+1 long, −1 short", "sign of first confirmed breakout", "unitless", "signal candle close"),
    "breakout_buffer_atr": ("Frozen candidate threshold", "buffer ∈ {0.25, 0.35, 0.45, 0.55}", "ATR multiples", "configured before session"),
    "or_high": ("Opening-range high", "max M1 high", "USD/oz", "[OR start, OR end)"),
    "or_low": ("Opening-range low", "min M1 low", "USD/oz", "[OR start, OR end)"),
    "or_width_price": ("Opening-range width", "OR high − OR low", "USD/oz", "[OR start, OR end)"),
    "or_width_pct": ("OR width relative to first OR open", "(OR high − OR low) / first OR M1 open", "fraction", "[OR start, OR end)"),
    "or_width_atr": ("OR width relative to prior ATR", "OR width / prior UTC-day ATR(14)", "ATR multiples", "OR and completed previous UTC days"),
    "or_return": ("OR first open to last close return", "last OR M1 close / first OR M1 open − 1", "fraction", "[OR start, OR end)"),
    "or_body_direction": ("Sign of OR open-to-close move", "sign(last OR M1 close − first OR M1 open)", "−1, 0, +1", "[OR start, OR end)"),
    "or_close_location": ("OR close location within its extremes", "(last OR close − OR low) / OR width", "fraction", "[OR start, OR end)"),
    "atr_at_entry": ("Strategy ATR available to this session", "14-period EWM of UTC daily true ranges, shifted one completed UTC day", "USD/oz", "completed UTC days strictly before current UTC day"),
    "atr_relative_to_price": ("Prior ATR divided by signal close", "prior ATR / breakout M5 close", "fraction", "prior UTC days plus completed breakout M5"),
    "breakout_distance_price": ("Close beyond OR boundary, direction-aware", "long: close − OR high; short: OR low − close", "USD/oz", "completed breakout M5 and OR"),
    "breakout_strength_atr": ("Breakout distance in ATR", "breakout distance / prior ATR", "ATR multiples", "completed breakout M5, OR and prior UTC days"),
    "breakout_candle_range_atr": ("Breakout candle high-low in ATR", "(M5 high − M5 low) / prior ATR", "ATR multiples", "completed breakout M5"),
    "breakout_body_atr": ("Absolute breakout candle body in ATR", "abs(M5 close − M5 open) / prior ATR", "ATR multiples", "completed breakout M5"),
    "breakout_body_ratio": ("Body share of breakout candle range", "abs(M5 close − M5 open) / (M5 high − M5 low)", "fraction", "completed breakout M5"),
    "breakout_close_location": ("Directional close location; 1 means at breakout extreme", "long: (close − low) / range; short: (high − close) / range", "fraction", "completed breakout M5"),
    "breakout_wick_against_direction_atr": ("Wick opposite breakout direction", "long: lower wick / ATR; short: upper wick / ATR", "ATR multiples", "completed breakout M5"),
    "breakout_wick_with_direction_atr": ("Wick in breakout direction", "long: upper wick / ATR; short: lower wick / ATR", "ATR multiples", "completed breakout M5"),
    "minutes_since_or_end": ("Elapsed minutes from OR completion to signal close", "signal close UTC − OR end UTC", "minutes", "signal timestamp"),
    "minutes_since_session_open": ("Elapsed minutes from local 08:20 session open", "signal close UTC − OR start UTC", "minutes", "signal timestamp"),
    "entry_hour_ny": ("New York hour at signal/entry boundary", "hour(signal close converted to America/New_York)", "hour 0–23", "signal timestamp"),
    "entry_minute_ny": ("New York minute at signal/entry boundary", "minute(signal close converted to America/New_York)", "minute 0–59", "signal timestamp"),
    "day_of_week": ("New York weekday", "Monday=0 … Sunday=6", "integer", "signal timestamp"),
    "pre_or_return_30m": ("Pre-OR 30 one-minute-close returns", "close(OR start−1m) / close(OR start−31m) − 1", "fraction", "31 contiguous M1 bars ending before OR start"),
    "pre_or_return_60m": ("Pre-OR 60 one-minute-close returns", "close(OR start−1m) / close(OR start−61m) − 1", "fraction", "61 contiguous M1 bars ending before OR start"),
    "pre_or_return_120m": ("Pre-OR 120 one-minute-close returns", "close(OR start−1m) / close(OR start−121m) − 1", "fraction", "121 contiguous M1 bars ending before OR start"),
    "return_from_session_open": ("Signal price versus first OR M1 open", "breakout M5 close / first OR M1 open − 1", "fraction", "OR first open through completed breakout M5"),
    "distance_from_session_open_atr": ("Signed price distance from session open", "(breakout M5 close − first OR M1 open) / prior ATR", "ATR multiples", "OR first open through completed breakout M5"),
    "realized_volatility_before_entry": ("Unannualized stdev of 60 M1 log returns", "sample stdev of 60 consecutive log(close_t / close_t−1)", "log-return per minute", "61 contiguous M1 closes ending at signal close−1m"),
    "price_vs_ema20_atr": ("Signal close versus causal M5 EMA20", "(signal close − EMA20) / prior ATR", "ATR multiples", "complete M5 closes from development start through breakout candle"),
    "price_vs_ema50_atr": ("Signal close versus causal M5 EMA50", "(signal close − EMA50) / prior ATR", "ATR multiples", "complete M5 closes from development start through breakout candle"),
    "ema20_slope_atr": ("Change in causal EMA20 from previous complete M5", "(current EMA20 − previous complete-bar EMA20) / prior ATR", "ATR multiples per complete bar", "complete M5 closes through breakout candle"),
}

OTHER_DEFINITIONS = {
    "event_id": ("Unique session and buffer event key", "symbol|session|NY date|buffer", "text", "session and frozen buffer"),
    "session_id": ("Shared identifier across buffers on same NY session", "symbol|session|NY date", "text", "session calendar"),
    "date": ("New York session date", "strategy session_date", "YYYY-MM-DD", "session calendar"),
    "buffer": ("Frozen candidate buffer identity", "configured ATR multiple", "ATR multiples", "configured before session"),
    "direction": ("First confirmed breakout side", "long or short", "category", "completed breakout M5"),
    "year": ("NY session year for future chronological grouping", "year(date)", "year", "session calendar"),
    "wf_fold_role": ("All prespecified train/test fold memberships; never a predictor", "pipe-separated train_N/test_N roles", "text", "session year"),
    "signal_time_utc": ("Close timestamp of breakout M5 bar", "signal_open + 5m", "UTC timestamp", "completed breakout M5"),
    "entry_time_utc": ("Open timestamp of next M1 bar; equal to signal close", "backtester trade timestamp", "UTC timestamp", "entry boundary"),
    "signal_time_ny": ("Signal timestamp in New York zone", "timezone conversion", "NY timestamp", "completed breakout M5"),
    "entry_time_ny": ("Entry timestamp in New York zone", "timezone conversion", "NY timestamp", "entry boundary"),
    "or_start_utc": ("Opening-range first minute", "strategy range_start", "UTC timestamp", "session definition"),
    "or_end_utc": ("Opening-range end, exclusive", "strategy range_end", "UTC timestamp", "session definition"),
    "entry_price": ("Actual simulated fill including spread/slippage", "backtester entry", "USD/oz", "entry M1 open and costs"),
    "stop_price": ("Initial opposite-OR stop", "strategy stop", "USD/oz", "entry fill and OR"),
    "target_price": ("Nominal 2R target", "entry ± 2 × initial stop distance", "USD/oz", "entry fill and stop"),
    "exit_price": ("Simulated final exit fill", "backtester exit", "USD/oz", "post-entry M1 path"),
    "exit_time_utc": ("Simulated exit minute", "backtester exit_time", "UTC timestamp", "post-entry M1 path"),
    "ambiguous_m1": ("Both stop and target touched same M1 bar", "backtester conservative ambiguity flag", "boolean", "post-entry M1 path"),
    "exit_reason": ("Stop, target, gap, or session-time exit", "backtester exit_reason", "category", "post-entry M1 path"),
    "final_R": ("Net outcome including commission", "net P/L / estimated initial risk", "R", "complete trade"),
    "net_pnl": ("Net simulated USD P/L", "gross P/L − commission", "USD", "complete trade"),
    "MAE_R": ("Backtester adverse excursion approximation", "max adverse observed before exit / initial stop distance", "R", "post-entry M1 path"),
    "MFE_R": ("Backtester favorable excursion approximation", "max favorable observed before exit / initial stop distance", "R", "post-entry M1 path"),
    "trade_positive": ("Positive final net R", "1 if final_R > 0 else 0", "0/1", "complete trade"),
    "reached_0_5R": ("Approximate favorable excursion reached 0.5R", "1 if MFE_R >= 0.5 else 0", "0/1", "post-entry M1 path"),
    "reached_1R": ("Approximate favorable excursion reached 1R", "1 if MFE_R >= 1 else 0", "0/1", "post-entry M1 path"),
    "reached_2R": ("Approximate favorable excursion reached 2R", "1 if MFE_R >= 2 else 0", "0/1", "post-entry M1 path"),
    "stopped_out": ("Exit reason begins with stop", "1 for stop or stop_gap", "0/1", "complete trade"),
    "session_exit": ("Exit at 16:00 New York session boundary", "1 if exit_reason == time else 0", "0/1", "complete trade"),
}


def _table(headers: list[str], rows: list[list[object]]) -> str:
    def clean(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" +
            "".join("| " + " | ".join(clean(v) for v in row) + " |\n" for row in rows))


def dictionary_markdown() -> str:
    if set(FEATURE_DEFINITIONS) != set(FEATURE_COLUMNS):
        raise AssertionError("feature dictionary incomplete")
    defs = {**OTHER_DEFINITIONS, **FEATURE_DEFINITIONS}
    all_columns = IDENTIFIER_COLUMNS + FEATURE_COLUMNS + EXECUTION_COLUMNS + LABEL_COLUMNS
    if set(defs) != set(all_columns):
        raise AssertionError("column dictionary incomplete")
    rows = []
    for name in all_columns:
        category = ("FEATURE" if name in FEATURE_COLUMNS else "IDENTIFIER" if name in IDENTIFIER_COLUMNS
                    else "EXECUTION_METADATA" if name in EXECUTION_COLUMNS else "LABEL")
        desc, formula, units, window = defs[name]
        latest = ("before OR start (frozen configuration)" if name == "breakout_buffer_atr" else
                  "signal close" if name in FEATURE_COLUMNS and name not in
                  {"or_high", "or_low", "or_width_price", "or_width_pct", "or_width_atr", "or_return",
                   "or_body_direction", "or_close_location", "atr_at_entry", "pre_or_return_30m",
                   "pre_or_return_60m", "pre_or_return_120m"} else
                  "OR end" if name.startswith("or_") else
                  "current UTC-day start; ATR uses previous completed UTC day" if name == "atr_at_entry" else
                  "OR start" if name.startswith("pre_or_") else "not a predictor")
        rows.append([name, category, desc, formula, units, window, latest])
    return ("# ORB event-feature data dictionary\n\n"
            "Rows are completed candidate trades from 2018–2024 only. The M5 signal close and next M1 entry "
            "open share a timestamp. All market-input M1 bars used in features start before that boundary. "
            "OR values finish at OR end, and strategy ATR uses only completed previous UTC days. "
            "Missing values are left empty when a required contiguous window or EMA warm-up is unavailable.\n\n"
            "The breakout close location is direction-normalized; pre-OR and session-open returns are raw signed returns. "
            "EMA uses complete M5 candles only, EWM adjust=False, min_periods equal to 20/50, with no centered "
            "window or future fill. EMA slope uses the previous complete M5 bar, even across a data gap. "
            "Overnight return is omitted because the broker's XAUUSD trading-day boundary is not frozen here. "
            "MFE/MAE and reached-R labels use the existing M1 bar execution approximation: intraminute excursion "
            "after an exit is unknown, and a target exit can have MFE below 2R after slippage. These labels are "
            "outcomes, not predictors. Buffer siblings on one date are dependent observations.\n\n" +
            _table(["Column", "Class", "Definition", "Formula", "Units", "Exact data window", "Latest information timestamp used"], rows))


def validation_markdown(frame: pd.DataFrame) -> str:
    features = frame[list(FEATURE_COLUMNS)].select_dtypes(include="number")
    lines = ["# ORB event-feature validation", "",
             "Descriptive development-period checks only. Correlation with final R is exploratory and is not proof of predictive value.", "",
             f"Rows: **{len(frame):,}**  ", f"Sessions: **{frame.session_id.nunique():,}**  ",
             f"Duplicate event IDs: **{int(frame.event_id.duplicated().sum())}**", ""]
    for title, series in (("Rows by year", frame.year.value_counts().sort_index()),
                          ("Rows by buffer", frame.buffer.value_counts().sort_index()),
                          ("Rows by direction", frame.direction.value_counts().sort_index()),
                          ("Buffer events per session", frame.groupby("session_id").size().value_counts().sort_index())):
        lines.extend([f"## {title}", "", _table(["Value", "Rows"], [[k, int(v)] for k, v in series.items()]), ""])
    missing = frame[list(FEATURE_COLUMNS)].isna().mean() * 100
    stats = features.agg(["min", "median", "max"])
    lines.extend(["## Feature coverage and range", "",
                  _table(["Feature", "Missing %", "Min", "Median", "Max"],
                         [[name, f"{missing[name]:.2f}",
                           *[f"{stats.loc[metric, name]:.6g}" if pd.notna(stats.loc[metric, name]) else "NA"
                             for metric in ("min", "median", "max")]] for name in FEATURE_COLUMNS]), ""])
    corr = features.corr()
    lines.extend(["## Numeric feature correlation matrix", "",
                  _table(["Feature", *corr.columns.tolist()],
                         [[name, *[f"{value:.3f}" if pd.notna(value) else "NA" for value in corr.loc[name]]]
                          for name in corr.index]), ""])
    target_corr = features.corrwith(frame.final_R)
    lines.extend(["## Feature correlation with final R", "",
                  _table(["Feature", "Pearson r"],
                         [[name, f"{value:.4f}" if pd.notna(value) else "NA"]
                          for name, value in target_corr.items()]), ""])
    for title, grouping in (("Profitable vs nonprofitable", "trade_positive"),
                            ("Long vs short", "direction"), ("By buffer", "buffer")):
        rows = []
        for group_name, group in frame.groupby(grouping, sort=True):
            for name in FEATURE_COLUMNS:
                values = group[name].dropna()
                quantiles = values.quantile([0.25, 0.5, 0.75]) if len(values) else pd.Series(dtype=float)
                rows.append([group_name, name, len(values),
                             *[f"{quantiles.loc[q]:.5g}" if q in quantiles.index else "NA"
                               for q in (0.25, 0.5, 0.75)]])
        lines.extend([f"## {title}: feature distributions", "",
                      "P25, median and P75 summarize each group; counts exclude missing values.", "",
                      _table(["Group", "Feature", "N", "P25", "Median", "P75"], rows), ""])
    return "\n".join(lines) + "\n"
