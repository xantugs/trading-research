"""Descriptive stability analysis of the sealed-development ORB event table.

No model is fitted and no bin boundary becomes a trading rule. All bins are
fixed from the complete 2018–2024 development table before group comparisons.
"""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
import pandas as pd

from .ml_features import FEATURE_COLUMNS
from .walk_forward import CANDIDATE_BUFFERS


ALIGNED_SOURCES = OrderedDict([
    ("trend_alignment_ema20", "price_vs_ema20_atr"),
    ("trend_alignment_ema50", "price_vs_ema50_atr"),
    ("session_return_aligned", "return_from_session_open"),
    ("pre_or_return_30m_aligned", "pre_or_return_30m"),
    ("pre_or_return_60m_aligned", "pre_or_return_60m"),
    ("pre_or_return_120m_aligned", "pre_or_return_120m"),
    ("ema20_slope_aligned", "ema20_slope_atr"),
])

SHORTLIST = (
    "minutes_since_or_end", "minutes_since_session_open", "or_width_atr", "or_width_pct",
    "or_return", "or_close_location", "breakout_strength_atr", "breakout_candle_range_atr",
    "breakout_body_atr", "breakout_body_ratio", "breakout_close_location",
    "breakout_wick_against_direction_atr", "breakout_wick_with_direction_atr",
    "atr_relative_to_price", "realized_volatility_before_entry",
    "pre_or_return_30m_aligned", "pre_or_return_60m_aligned", "pre_or_return_120m_aligned",
    "session_return_aligned", "trend_alignment_ema20", "trend_alignment_ema50",
    "ema20_slope_aligned",
)
ROBUSTNESS_FEATURES = (
    "minutes_since_or_end", "or_width_atr", "breakout_strength_atr", "breakout_body_ratio",
    "pre_or_return_120m_aligned", "session_return_aligned", "trend_alignment_ema20",
    "trend_alignment_ema50",
)
OUTCOMES = ("final_R", "MAE_R", "MFE_R", "trade_positive")
MIN_ROBUSTNESS_ROWS = 20


def prepare_events(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate the CSV schema and derive direction-aligned columns in memory."""
    required = {"event_id", "session_id", "date", "buffer", "direction", "direction_sign",
                "year", "signal_time_utc", "entry_time_utc", "exit_time_utc", "final_R",
                "net_pnl", "MAE_R", "MFE_R", "trade_positive", *FEATURE_COLUMNS}
    missing = required - set(raw)
    if missing:
        raise ValueError(f"event dataset missing required columns: {sorted(missing)}")
    if raw.empty or raw.event_id.isna().any() or raw.session_id.isna().any():
        raise ValueError("event dataset or its identifiers are empty")
    if raw.event_id.duplicated().any():
        raise ValueError("event_id must be unique")
    frame = raw.copy()
    dates = pd.to_datetime(frame.date, errors="raise")
    if dates.isna().any() or dates.max() >= pd.Timestamp("2025-01-01"):
        raise ValueError("2025+ or missing session dates are forbidden")
    for name in ("signal_time_utc", "entry_time_utc", "exit_time_utc"):
        stamps = pd.to_datetime(frame[name], utc=True, errors="raise")
        if stamps.isna().any() or stamps.max() >= pd.Timestamp("2025-01-01", tz="UTC"):
            raise ValueError(f"2025+ or missing {name} is forbidden")
    if not frame.year.eq(dates.dt.year).all() or not set(frame.year.unique()).issubset(set(range(2018, 2025))):
        raise ValueError("year and New York session date disagree")
    if set(frame.buffer.unique()) != set(CANDIDATE_BUFFERS):
        raise ValueError("event dataset must contain exactly the four frozen buffers")
    if not set(frame.direction.unique()).issubset({"long", "short"}):
        raise ValueError("direction must be long or short")
    expected_sign = np.where(frame.direction.eq("long"), 1, -1)
    if not np.array_equal(frame.direction_sign.to_numpy(), expected_sign):
        raise ValueError("direction_sign does not match direction")
    if frame.groupby("session_id").date.nunique().gt(1).any():
        raise ValueError("session_id crosses New York dates")
    for name in (*FEATURE_COLUMNS, "buffer", "direction_sign", *OUTCOMES, "net_pnl"):
        frame[name] = pd.to_numeric(frame[name], errors="raise")
        if np.isinf(frame[name].to_numpy(dtype=float)).any():
            raise ValueError(f"infinite numeric values in {name}")
    if frame[["final_R", "net_pnl", "MAE_R", "MFE_R", "trade_positive"]].isna().any().any():
        raise ValueError("outcome labels must not be missing")
    if not frame.trade_positive.isin([0, 1]).all() or not np.array_equal(
            frame.trade_positive.to_numpy(), (frame.final_R > 0).astype(int).to_numpy()):
        raise ValueError("trade_positive disagrees with final_R")
    for derived, source in ALIGNED_SOURCES.items():
        if derived in frame.columns:
            raise ValueError(f"aligned name already present in source CSV: {derived}")
        frame[derived] = frame.direction_sign * frame[source]
    if not set(SHORTLIST).issubset(frame.columns):
        raise ValueError(f"shortlist unavailable: {sorted(set(SHORTLIST) - set(frame))}")
    forbidden = ("exit", "future", "mae", "mfe", "final_r", "pnl")
    if any(term in name.lower() for name in (*FEATURE_COLUMNS, *ALIGNED_SOURCES) for term in forbidden):
        raise ValueError("outcome-derived name found in predictor set")
    return frame


def make_bins(frame: pd.DataFrame) -> dict[str, dict]:
    """Global development quantiles; repeated values reduce actual bin count."""
    result = {}
    for feature in SHORTLIST:
        values = frame[feature]
        valid = values.dropna()
        codes = pd.Series(pd.NA, index=frame.index, dtype="Int64")
        edges = np.array([], dtype=float)
        if valid.nunique() >= 2:
            for requested in range(5, 1, -1):
                _, raw_edges = pd.qcut(valid, q=requested, retbins=True,
                                       duplicates="drop", precision=15)
                candidate_edges = np.unique(raw_edges)
                if len(candidate_edges) >= 3:
                    edges = candidate_edges
                    assigned = pd.cut(valid, bins=edges, labels=False, include_lowest=True)
                    codes.loc[valid.index] = assigned.astype("Int64") + 1
                    break
        result[feature] = {"codes": codes, "edges": edges, "actual_bins": max(0, len(edges) - 1),
                           "excluded_missing_rows": int(values.isna().sum())}
    return result


def _profit_factor(pnl: pd.Series) -> float:
    wins = float(pnl.loc[pnl > 0].sum())
    losses = float(-pnl.loc[pnl < 0].sum())
    return wins / losses if losses > 0 else np.nan


def _spearman(left: pd.Series, right: pd.Series) -> float:
    """Pearson correlation of average ranks; avoids an optional SciPy dependency."""
    return _pearson(left.rank(method="average"), right.rank(method="average"))


def _pearson(left: pd.Series, right: pd.Series) -> float:
    if len(left) < 3 or left.nunique() < 2 or right.nunique() < 2:
        return np.nan
    return float(left.corr(right))


def _bin_stats(part: pd.DataFrame) -> dict:
    if part.empty:
        return {"rows": 0, "unique_sessions": 0, "mean_R": np.nan, "median_R": np.nan,
                "profit_factor": np.nan, "positive_rate": np.nan, "mean_MAE_R": np.nan,
                "mean_MFE_R": np.nan}
    return {"rows": len(part), "unique_sessions": part.session_id.nunique(),
            "mean_R": float(part.final_R.mean()), "median_R": float(part.final_R.median()),
            "profit_factor": _profit_factor(part.net_pnl),
            "positive_rate": float(part.trade_positive.mean()),
            "mean_MAE_R": float(part.MAE_R.mean()), "mean_MFE_R": float(part.MFE_R.mean())}


def _groups(frame: pd.DataFrame):
    yield "pooled", "all", frame
    for year in range(2018, 2025):
        yield "year", str(year), frame.loc[frame.year == year]
    for buffer in CANDIDATE_BUFFERS:
        yield "buffer", f"{buffer:.2f}", frame.loc[frame.buffer == buffer]
    for direction in ("long", "short"):
        yield "direction", direction, frame.loc[frame.direction == direction]


def bin_tables(frame: pd.DataFrame, bins: dict[str, dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    bin_rows, group_rows = [], []
    for feature in SHORTLIST:
        spec = bins[feature]
        codes, edges, k = spec["codes"], spec["edges"], spec["actual_bins"]
        for scope, scope_value, group in _groups(frame):
            group_codes = codes.loc[group.index]
            excluded = int(group[feature].isna().sum())
            metrics = []
            for ordinal in range(1, k + 1):
                part = group.loc[group_codes == ordinal]
                stats = _bin_stats(part)
                metrics.append(stats)
                bin_rows.append({"feature": feature, "scope": scope, "scope_value": scope_value,
                                 "bin": f"Q{ordinal}", "bin_ordinal": ordinal,
                                 "bin_lower": float(edges[ordinal - 1]),
                                 "bin_upper": float(edges[ordinal]), "actual_bins": k,
                                 "reference_rows": len(group), "excluded_missing_rows": excluded,
                                 **stats})
            first, last = (metrics[0], metrics[-1]) if k >= 2 else (_bin_stats(group.iloc[:0]),) * 2
            has_extremes = first["rows"] > 0 and last["rows"] > 0 and k >= 2
            effect = (last["mean_R"] - first["mean_R"]) if has_extremes else np.nan
            means = np.array([item["mean_R"] for item in metrics], dtype=float)
            if k >= 2 and np.isfinite(means).all():
                slope = _spearman(pd.Series(range(1, k + 1)), pd.Series(means))
                diffs = np.diff(means)
                monotonic = ("increasing" if np.all(diffs >= 0) else
                             "decreasing" if np.all(diffs <= 0) else "nonmonotonic")
            else:
                slope, monotonic = np.nan, "insufficient bins"
            group_rows.append({
                "feature": feature, "scope": scope, "scope_value": scope_value,
                "actual_bins": k, "reference_rows": len(group), "excluded_missing_rows": excluded,
                "unique_sessions": group.session_id.nunique(),
                "lowest_bin_rows": first["rows"], "highest_bin_rows": last["rows"],
                "top_minus_bottom_mean_R": effect,
                "q5_minus_q1_mean_R": effect if k == 5 else np.nan,
                "top_minus_bottom_positive_rate": (last["positive_rate"] - first["positive_rate"]
                                                   if has_extremes else np.nan),
                "top_minus_bottom_mean_MFE_R": (last["mean_MFE_R"] - first["mean_MFE_R"]
                                                 if has_extremes else np.nan),
                "top_minus_bottom_mean_MAE_R": (last["mean_MAE_R"] - first["mean_MAE_R"]
                                                 if has_extremes else np.nan),
                "bin_mean_R_spearman": slope, "bin_mean_R_monotonic": monotonic,
            })
    return pd.DataFrame(bin_rows), pd.DataFrame(group_rows)


def pairwise_table(frame: pd.DataFrame) -> pd.DataFrame:
    features = tuple(FEATURE_COLUMNS) + tuple(ALIGNED_SOURCES)
    rows = []
    for i, first in enumerate(features):
        for second in features[i + 1:]:
            pair = frame[[first, second]].dropna()
            status = ("insufficient rows" if len(pair) < 3 else
                      "no variation" if pair[first].nunique() < 2 or pair[second].nunique() < 2 else "ok")
            pearson = _pearson(pair[first], pair[second])
            spearman = _spearman(pair[first], pair[second])
            rows.append({"feature_a": first, "feature_b": second,
                         "pearson_corr": pearson, "spearman_corr": spearman,
                         "flag_high_redundancy": bool(np.isfinite(pearson) and abs(pearson) >= 0.80),
                         "status": status,
                         "rows": len(pair), "unique_sessions": frame.loc[pair.index, "session_id"].nunique(),
                         "excluded_missing_rows": len(frame) - len(pair)})
    return pd.DataFrame(rows)


def outcome_correlations(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for feature in tuple(FEATURE_COLUMNS) + tuple(ALIGNED_SOURCES):
        for scope, scope_value, group in _groups(frame):
            for target in OUTCOMES:
                pair = group[[feature, target]].dropna()
                status = ("insufficient rows" if len(pair) < 3 else
                          "no variation" if pair[feature].nunique() < 2 or pair[target].nunique() < 2
                          else "ok")
                rows.append({"feature": feature, "scope": scope, "scope_value": scope_value,
                             "outcome": target, "pearson_corr": _pearson(pair[feature], pair[target]),
                             "spearman_corr": _spearman(pair[feature], pair[target]),
                             "status": status,
                             "rows": len(pair), "unique_sessions": group.loc[pair.index, "session_id"].nunique(),
                             "excluded_missing_rows": len(group) - len(pair)})
    return pd.DataFrame(rows)


def year_buffer_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for feature in ROBUSTNESS_FEATURES:
        for year in range(2018, 2025):
            for buffer in CANDIDATE_BUFFERS:
                group = frame.loc[(frame.year == year) & (frame.buffer == buffer)]
                pair = group[[feature, "final_R"]].dropna()
                enough = (len(pair) >= MIN_ROBUSTNESS_ROWS and pair[feature].nunique() >= 2 and
                          pair.final_R.nunique() >= 2)
                corr = _spearman(pair[feature], pair.final_R) if enough else np.nan
                rows.append({"feature": feature, "year": year, "buffer": buffer,
                             "rows": len(pair), "unique_sessions": group.loc[pair.index, "session_id"].nunique(),
                             "excluded_missing_rows": len(group) - len(pair),
                             "spearman_corr": corr,
                             "status": ("informative count" if enough and np.isfinite(corr)
                                        else "insufficient sample" if len(pair) < MIN_ROBUSTNESS_ROWS
                                        else "no variation")})
    return pd.DataFrame(rows)


def session_block_bootstrap(frame: pd.DataFrame, bins: dict[str, dict], *,
                            samples: int = 10_000, seed: int = 20260917) -> pd.DataFrame:
    """Resample whole session IDs; keep all of a session's buffer events together.

    Bin boundaries stay fixed at their exploratory full-development values.
    This uncertainty does not account for searching many features or boundaries.
    """
    if samples < 10_000:
        raise ValueError("the prespecified bootstrap requires at least 10,000 session samples")
    session_codes, sessions = pd.factorize(frame.session_id, sort=True)
    n_sessions, n_features = len(sessions), len(SHORTLIST)
    outcome = frame.final_R.to_numpy(dtype=float)
    low_sums = np.zeros((n_sessions, n_features))
    high_sums = np.zeros_like(low_sums)
    low_counts = np.zeros_like(low_sums)
    high_counts = np.zeros_like(low_sums)
    eligible = np.zeros(n_features, dtype=bool)
    for j, feature in enumerate(SHORTLIST):
        spec = bins[feature]
        if spec["actual_bins"] < 2:
            continue
        codes = spec["codes"].to_numpy(dtype="float64", na_value=np.nan)
        low, high = codes == 1, codes == spec["actual_bins"]
        if not low.any() or not high.any():
            continue
        eligible[j] = True
        low_sums[:, j] = np.bincount(session_codes[low], weights=outcome[low], minlength=n_sessions)
        high_sums[:, j] = np.bincount(session_codes[high], weights=outcome[high], minlength=n_sessions)
        low_counts[:, j] = np.bincount(session_codes[low], minlength=n_sessions)
        high_counts[:, j] = np.bincount(session_codes[high], minlength=n_sessions)

    effects = np.full((samples, n_features), np.nan)
    rng = np.random.default_rng(seed)
    chunk_size = 256
    for start in range(0, samples, chunk_size):
        count = min(chunk_size, samples - start)
        draws = rng.integers(0, n_sessions, size=(count, n_sessions), dtype=np.int32)
        multiplicity = np.empty((count, n_sessions), dtype=np.int16)
        for row in range(count):
            multiplicity[row] = np.bincount(draws[row], minlength=n_sessions)
        low_n, high_n = multiplicity @ low_counts, multiplicity @ high_counts
        with np.errstate(divide="ignore", invalid="ignore"):
            block_effects = (multiplicity @ high_sums) / high_n - (multiplicity @ low_sums) / low_n
        block_effects[:, ~eligible] = np.nan
        effects[start:start + count] = block_effects
    rows = []
    for j, feature in enumerate(SHORTLIST):
        valid = effects[:, j][np.isfinite(effects[:, j])]
        rows.append({"feature": feature, "actual_bins": bins[feature]["actual_bins"],
                     "rows": len(frame) - bins[feature]["excluded_missing_rows"],
                     "unique_sessions": frame.loc[frame[feature].notna(), "session_id"].nunique(),
                     "excluded_missing_rows": bins[feature]["excluded_missing_rows"],
                     "sessions_sampled_per_draw": n_sessions, "bootstrap_samples_requested": samples,
                     "bootstrap_samples_valid": len(valid),
                     "bootstrap_mean_effect": float(valid.mean()) if len(valid) else np.nan,
                     "bootstrap_ci_low": float(np.quantile(valid, 0.025)) if len(valid) else np.nan,
                     "bootstrap_ci_high": float(np.quantile(valid, 0.975)) if len(valid) else np.nan,
                     "bootstrap_prob_effect_le_zero": float((valid <= 0).mean()) if len(valid) else np.nan})
    return pd.DataFrame(rows)


def _same_sign(values: pd.Series, reference: float) -> tuple[int, int]:
    valid = values.dropna()
    if not np.isfinite(reference) or reference == 0:
        return 0, len(valid)
    return int((np.sign(valid) == np.sign(reference)).sum()), len(valid)


def stability_summary(group_effects: pd.DataFrame, bootstrap: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for feature in SHORTLIST:
        subset = group_effects.loc[group_effects.feature == feature]
        pooled = subset.loc[subset.scope == "pooled"].iloc[0]
        years = subset.loc[subset.scope == "year"]
        buffers = subset.loc[subset.scope == "buffer"]
        directions = subset.loc[subset.scope == "direction"]
        year_same, year_total = _same_sign(years.top_minus_bottom_mean_R, pooled.top_minus_bottom_mean_R)
        buffer_same, buffer_total = _same_sign(buffers.top_minus_bottom_mean_R, pooled.top_minus_bottom_mean_R)
        direction_same, direction_total = _same_sign(directions.top_minus_bottom_mean_R,
                                                     pooled.top_minus_bottom_mean_R)
        long_effect = float(directions.loc[directions.scope_value == "long", "top_minus_bottom_mean_R"].iloc[0])
        short_effect = float(directions.loc[directions.scope_value == "short", "top_minus_bottom_mean_R"].iloc[0])
        if np.isfinite(long_effect) and np.isfinite(short_effect):
            direction_pattern = ("same sign" if np.sign(long_effect) == np.sign(short_effect)
                                 else "opposite signs")
        else:
            direction_pattern = "unavailable"
        boot = bootstrap.loc[bootstrap.feature == feature].iloc[0]
        rows.append({
            "feature": feature, "actual_bins": int(pooled.actual_bins),
            "excluded_missing_rows": int(pooled.excluded_missing_rows),
            "pooled_q5_minus_q1_R": pooled.q5_minus_q1_mean_R,
            "pooled_top_minus_bottom_R": pooled.top_minus_bottom_mean_R,
            "pooled_top_minus_bottom_positive_rate": pooled.top_minus_bottom_positive_rate,
            "pooled_top_minus_bottom_mean_MFE_R": pooled.top_minus_bottom_mean_MFE_R,
            "pooled_top_minus_bottom_mean_MAE_R": pooled.top_minus_bottom_mean_MAE_R,
            "years_top_mean_R_gt_bottom": int((years.top_minus_bottom_mean_R > 0).sum()),
            "years_same_effect_direction": year_same, "years_total": year_total,
            "years_monotonic_increasing": int((years.bin_mean_R_monotonic == "increasing").sum()),
            "years_monotonic_decreasing": int((years.bin_mean_R_monotonic == "decreasing").sum()),
            "buffers_same_effect_direction": buffer_same, "buffers_total": buffer_total,
            "long_effect": long_effect, "short_effect": short_effect,
            "directions_same_effect_direction": direction_same, "directions_total": direction_total,
            "direction_pattern": direction_pattern,
            "bootstrap_mean_effect": boot.bootstrap_mean_effect,
            "bootstrap_ci_low": boot.bootstrap_ci_low,
            "bootstrap_ci_high": boot.bootstrap_ci_high,
            "bootstrap_prob_effect_le_zero": boot.bootstrap_prob_effect_le_zero,
            "bootstrap_samples_valid": int(boot.bootstrap_samples_valid),
        })
    return pd.DataFrame(rows)


def analyze_features(raw: pd.DataFrame, *, bootstrap_samples: int = 10_000,
                     seed: int = 20260917) -> dict[str, pd.DataFrame | dict]:
    frame = prepare_events(raw)
    bins = make_bins(frame)
    bin_frame, group_effects = bin_tables(frame, bins)
    bootstrap = session_block_bootstrap(frame, bins, samples=bootstrap_samples, seed=seed)
    return {
        "events": frame, "bin_specs": bins, "bins": bin_frame, "group_effects": group_effects,
        "stability": stability_summary(group_effects, bootstrap), "bootstrap": bootstrap,
        "pairwise": pairwise_table(frame), "correlations": outcome_correlations(frame),
        "year_buffer": year_buffer_table(frame),
    }
