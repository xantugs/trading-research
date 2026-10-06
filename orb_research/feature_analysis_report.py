"""Readable, prespecified description of development-only feature stability."""
from __future__ import annotations

import pandas as pd

from .feature_analysis import (ALIGNED_SOURCES, MIN_ROBUSTNESS_ROWS, ROBUSTNESS_FEATURES,
                               SHORTLIST)


def _number(value, digits: int = 3, signed: bool = False) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _table(headers: list[str], rows: list[list[object]]) -> str:
    def clean(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" +
            "".join("| " + " | ".join(clean(value) for value in row) + " |\n" for row in rows))


def build_report(result: dict, *, source_name: str, bootstrap_samples: int) -> str:
    frame: pd.DataFrame = result["events"]
    groups: pd.DataFrame = result["group_effects"]
    stable: pd.DataFrame = result["stability"]
    boot: pd.DataFrame = result["bootstrap"]
    pairwise: pd.DataFrame = result["pairwise"]
    correlation: pd.DataFrame = result["correlations"]
    year_buffer: pd.DataFrame = result["year_buffer"]
    sessions = frame.session_id.nunique()
    multiplicity = frame.groupby("session_id").size().value_counts().sort_index()
    missing = [(name, int(frame[name].isna().sum())) for name in SHORTLIST]
    lines = [
        "# XAUUSD ORB feature stability, 2018–2024 development data", "",
        "## 1. Dataset summary", "",
        f"Source: `{source_name}`. The analysis read this event CSV once; it did not read M1, ticks, or 2025+ outcomes.", "",
        f"**{len(frame):,} candidate rows** from **{sessions:,} unique New York sessions**. "
        f"Dates run from {frame.date.min()} to {frame.date.max()}. "
        "All four ATR buffers are included; the source event CSV is unchanged.", "",
        _table(["Year", "Rows", "Unique sessions"],
               [[year, len(group), group.session_id.nunique()] for year, group in frame.groupby("year")]), "",
        _table(["Buffer", "Rows", "Unique sessions"],
               [[_number(buffer, 2), len(group), group.session_id.nunique()]
                for buffer, group in frame.groupby("buffer")]), "",
        "Missing values are excluded only from analyses of the affected feature. "
        "Each output table includes its own excluded-row count; no values are filled.", "",
        _table(["Feature", "Missing rows", "Missing %"],
               [[name, count, _number(100 * count / len(frame), 2)] for name, count in missing]), "",
        "## 2. Dependence structure", "",
        "Buffer siblings from a trading session are **not independent**. All rows retain `session_id`; "
        "year, buffer and direction tables report both event rows and unique sessions. "
        "The bootstrap resamples session IDs, carrying every sibling row in a sampled session. "
        "No random row split is used.", "",
        _table(["Buffer events per session", "Sessions"],
               [[int(count), int(number)] for count, number in multiplicity.items()]), "",
        "## 3. Direction-normalized feature definitions", "",
        "`direction_sign` is +1 for long and −1 for short. The aligned feature is "
        "`direction_sign × raw feature`; positive means the raw move or trend points in the breakout direction. "
        "Original raw columns remain unchanged in the source dataset. Alignment is arithmetic only and uses "
        "no outcome information.", "",
        _table(["Derived feature", "Raw feature", "Formula"],
               [[derived, source, f"direction_sign × {source}"]
                for derived, source in ALIGNED_SOURCES.items()]), "",
        "## 4. Pooled feature effects", "",
        "Quintile boundaries come from the full 2018–2024 development feature distribution. "
        "Duplicate values can reduce the bin count without jitter. `Q5−Q1` is reported only when five "
        "bins exist; `top−bottom` always means the highest available bin minus the lowest. "
        "The same boundaries are used in all subgroup tables. Effects below are descriptive, not trading rules.", "",
        _table(["Feature", "Bins", "Missing", "Q5−Q1 mean R", "Top−bottom positive rate", "Pearson r with R", "Spearman ρ with R"],
               [[row.feature, int(row.actual_bins), int(row.excluded_missing_rows),
                 _number(row.pooled_q5_minus_q1_R, 4, True),
                 _number(row.pooled_top_minus_bottom_positive_rate, 3, True),
                 _number(correlation.loc[(correlation.feature == row.feature) &
                                         (correlation.scope == "pooled") &
                                         (correlation.outcome == "final_R"), "pearson_corr"].iloc[0], 3, True),
                 _number(correlation.loc[(correlation.feature == row.feature) &
                                         (correlation.scope == "pooled") &
                                         (correlation.outcome == "final_R"), "spearman_corr"].iloc[0], 3, True)]
                for row in stable.itertuples(index=False)]), "",
        "Full bin statistics, including median R, net-P/L profit factor, MAE and MFE, are in "
        "`feature_bins.csv`. `feature_correlations.csv` has Pearson and Spearman coefficients "
        "for final R, MAE, MFE and positive-trade rate, pooled and within each direction and buffer. "
        "Undefined correlations for constant or undersized groups are blank and labeled in the "
        "CSV `status` column. Correlations do not establish predictive value.", "",
        "## 5. Stability by year", "",
        "The sign count compares each year's top-minus-bottom mean R with the pooled sign. "
        "The Spearman coefficient below relates **bin ordinal to bin mean R within each year**, "
        "not individual trades. Exact annual effects and monotonicity flags are in "
        "`feature_group_effects.csv`.", "",
        _table(["Feature", "Years same sign", "Years top > bottom", "Monotonic up", "Monotonic down", "Median annual bin ρ"],
               [[row.feature, f"{row.years_same_effect_direction}/{row.years_total}",
                 row.years_top_mean_R_gt_bottom, row.years_monotonic_increasing,
                 row.years_monotonic_decreasing,
                 _number(groups.loc[(groups.feature == row.feature) & (groups.scope == "year"),
                                    "bin_mean_R_spearman"].median(), 3, True)]
                for row in stable.itertuples(index=False)]), "",
        "## 6. Stability by buffer", "",
        "These are top-minus-bottom mean-R differences **inside each buffer**, using the pooled "
        "feature bins. `feature_bins.csv` records the event and unique-session counts for every bin.", "",
        _table(["Feature", "0.25", "0.35", "0.45", "0.55", "Same sign as pooled"],
               [[row.feature,
                 *[_number(groups.loc[(groups.feature == row.feature) &
                                      (groups.scope == "buffer") &
                                      (groups.scope_value == f"{buffer:.2f}"),
                                      "top_minus_bottom_mean_R"].iloc[0], 4, True)
                   for buffer in (0.25, 0.35, 0.45, 0.55)],
                 f"{row.buffers_same_effect_direction}/{row.buffers_total}"]
                for row in stable.itertuples(index=False)]), "",
        "## 7. Stability by direction", "",
        "Positive aligned values have the same directional interpretation for longs and shorts. "
        "Different long/short effects still cannot justify disabling either side.", "",
        _table(["Feature", "Long top−bottom R", "Short top−bottom R", "Observed sign pattern"],
               [[row.feature, _number(row.long_effect, 4, True), _number(row.short_effect, 4, True),
                 row.direction_pattern] for row in stable.itertuples(index=False)]), "",
        "## 8. Year × buffer consistency", "",
        f"Only the eight prespecified features are shown here. A cell needs at least "
        f"{MIN_ROBUSTNESS_ROWS} complete event rows and feature variation; smaller cells are marked "
        "`insufficient sample` in `feature_year_buffer.csv`. A qualifying cell may still share "
        "sessions with other buffers and is not an independent replication.", "",
    ]
    robustness_rows = []
    for feature in ROBUSTNESS_FEATURES:
        cells = year_buffer.loc[year_buffer.feature == feature]
        valid = cells.loc[cells.status == "informative count"]
        positive = int((valid.spearman_corr > 0).sum())
        negative = int((valid.spearman_corr < 0).sum())
        robustness_rows.append([feature, len(valid), len(cells),
                                _number(len(valid) / len(cells), 2), positive, negative,
                                _number(positive / len(valid), 2) if len(valid) else "n/a",
                                _number(negative / len(valid), 2) if len(valid) else "n/a"])
    lines.extend([_table(["Feature", "Informative cells", "All cells", "Informative fraction", "Positive", "Negative",
                          "Positive fraction", "Negative fraction"], robustness_rows), "",
                  "## 9. Session-block bootstrap uncertainty", "",
                  f"Each of {bootstrap_samples:,} draws resamples {sessions:,} `session_id` values with replacement. "
                  "Every selected ID contributes all its buffer events. The statistic is "
                  "highest-bin mean R minus lowest-bin mean R; the 95% interval is the empirical "
                  "2.5th–97.5th percentile. Boundaries remain fixed from the full development data. "
                  "The interval is exploratory and does not correct for searching 22 features.", "",
                  _table(["Feature", "Bootstrap mean", "95% low", "95% high", "Pr(effect ≤ 0)", "Valid draws"],
                         [[row.feature, _number(row.bootstrap_mean_effect, 4, True),
                           _number(row.bootstrap_ci_low, 4, True), _number(row.bootstrap_ci_high, 4, True),
                           _number(row.bootstrap_prob_effect_le_zero, 3),
                           int(row.bootstrap_samples_valid)] for row in boot.itertuples(index=False)]), "",
                  "## 10. Feature redundancy", "",
                  "`feature_pairwise_summary.csv` compares numeric predictors only, including raw and "
                  "direction-aligned features. It excludes all outcomes and execution fields. "
                  "Pairs with absolute Pearson correlation ≥ 0.80 are flagged; no feature is removed.", "",
                  f"**{int(pairwise.flag_high_redundancy.sum())} of {len(pairwise)} pairs flagged.**", ""])
    flagged = pairwise.loc[pairwise.flag_high_redundancy].head(20)
    lines.extend([_table(["Feature A", "Feature B", "Pearson r", "Spearman ρ", "Rows", "Excluded"],
                         [[row.feature_a, row.feature_b, _number(row.pearson_corr, 3, True),
                           _number(row.spearman_corr, 3, True), int(row.rows),
                           int(row.excluded_missing_rows)] for row in flagged.itertuples(index=False)]), "",
                  "## 11. Candidate hypotheses for future testing", "",
                  "The eight prespecified year × buffer checks below summarize signs only. They are "
                  "questions for a **future chronological, session-grouped walk-forward experiment**, "
                  "not selected filters or thresholds. No feature was dropped or ranked for trading.", "",
                  _table(["Prespecified feature", "Years same sign", "Buffers same sign", "Long/short signs", "Bootstrap 95% interval"],
                         [[row.feature, f"{row.years_same_effect_direction}/{row.years_total}",
                           f"{row.buffers_same_effect_direction}/{row.buffers_total}",
                           row.direction_pattern,
                           f"[{_number(row.bootstrap_ci_low, 3, True)}, {_number(row.bootstrap_ci_high, 3, True)}]"]
                          for row in stable.itertuples(index=False) if row.feature in ROBUSTNESS_FEATURES]), "",
                  f"In this development table, {int(((stable.bootstrap_ci_low <= 0) & (stable.bootstrap_ci_high >= 0)).sum())} "
                  f"of {len(stable)} session-block intervals include zero. The timing features "
                  "`minutes_since_or_end` and `minutes_since_session_open` have identical correlations "
                  "because the opening range is always 60 minutes; they are one relationship, "
                  "not two confirmations. `price_vs_ema20_atr` and `ema20_slope_atr` are also "
                  "mathematically redundant under the fixed EMA update. Apparent relationships "
                  "here remain candidates for a separately specified future test.", "",
                  "## 12. Warnings and limitations", "",
                  "- The 2018–2024 history was already used to choose the buffer family. Pooled bins and "
                  "all correlations inspect that same development history. None is a sealed test.",
                  "- Same-session buffer rows are dependent. Session-block resampling respects that "
                  "dependence but does not repair prior research selection or multiple comparisons.",
                  "- Annual, buffer and direction subgroup bins can be small. Read row and unique-session "
                  "counts before interpreting any apparent consistency.",
                  "- Profit factor uses modeled net USD P/L. M1-derived MAE/MFE and reached-R outcomes "
                  "are bar-resolution approximations; costs and fills are modeled, not verified broker fills.",
                  "- No result here defines an entry filter, direction exclusion, optimized threshold, "
                  "predictive model or live-trading recommendation. The 2025+ holdout was not read.", ""])
    return "\n".join(lines)
