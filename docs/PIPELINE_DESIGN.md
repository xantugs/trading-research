# ML trading research pipeline: design (9 October 2026)

**Purpose.** One pipeline for every future ML trading study (S2 regimes, news, factor
zoo), built so that the mistakes already made here cannot recur silently:
- a vendor clock off by an hour;
- bars labelled by their start, read as if known at that time;
- sample weights too small for XGBoost to split.

It follows the Kaggle lesson (trust validation, and make it hard to fool) and the
finance-ML practice of López de Prado (purging, embargo, combinatorial CV, deflated
Sharpe).

## Principles

1. **Point-in-time by construction.** Every value carries the time it became *known*. Code
   asks "what was known at t?", never "what is the bar at t?".
2. **One way to do each thing.** One accessor, one splitter catalogue, one cost model and
   one ledger. Studies compose them and do not re-implement them.
3. **Every run is recorded.** The number of trials is known, so the multiple-testing
   correction is automatic.
4. **The sealed test is enforced by code,** not by discipline: a vault that opens once.
5. **Baselines always run.** A model that cannot beat a linear model and the plain rule is
   not a result.
6. **Parity from research to live.** The live bot computes features with the same code as
   the backtest, and a test proves they match.

## Layers

### 1. Data (`pipeline/data`)
- **Immutable raw caches** with provenance: source, vendor clock and download manifest.
  This exists today for MT5 and HistData.
- **Normalisation to `known_at`.** Each bar is stored with `known_at` = bar end, in UTC,
  and accessors use only `known_at`. This removes the bar-start look-ahead at the source.
- **Clock verification as a gate.** Every new feed must pass:
  - cross-feed lag correlation against a trusted feed (peak at lag 0);
  - session-open volatility timing.

  Failing feeds are refused. This is the HistData DST check, made mandatory.
- **Calendar as data:** exchange sessions, half-days, holidays and DST, used by every
  layer.

### 2. Features (`pipeline/features`)
- **Declared features:** each has a name, its inputs, its lookback and a function of
  point-in-time data. A registry hashes the definition and caches results by (feature
  hash, data hash).
- **Access only through `known_at(series, t)`,** which returns the last value with
  `known_at ≤ t`. There is no raw indexing.
- **Automatic leakage tests for every feature set:**
  - a future-perturbation test: rewrite each input after t, and the features at t must
    not change (generalised from `test_no_feature_reads_any_input_at_or_after_its_checkpoint`);
  - a lag-ablation test: shift each input back 1, 2 and 5 minutes. A sharp collapse in
    predictive power is flagged as likely leakage (the DAX check, made routine).

### 3. Labels and samples (`pipeline/labels`)
- **Labels are forward returns or trade outcomes,** each with an explicit horizon and the
  cost model applied.
- **Purge and embargo windows** are derived from the label horizon and feature lookback,
  never typed by hand.
- **Sample weights are normalised to mean 1** before any model sees them, and a check
  enforces it.

### 4. Validation (`pipeline/validate`)
- **Splitter catalogue:**
  - walk-forward (expanding or rolling);
  - purged K-fold with embargo;
  - combinatorial purged CV (CPCV), for a *distribution* of out-of-sample Sharpe ratios
    rather than one number.
- **Nested validation** for any hyperparameter search. The outer folds never see the
  tuning.
- **Adversarial validation:** a classifier separating train from test periods flags
  distribution shift before it is trusted.
- **The vault.** Sealed date ranges are listed in `vault.json`.
  - Loaders refuse them unless an experiment is "unsealed".
  - Unsealing needs a committed plan (its git hash is checked) and is logged.
  - Each experiment can unseal each range once.

### 5. Models (`pipeline/models`)
- **Plug-in interface** (`fit`, `predict_proba`) with built-in sanity checks:
  - no-split and constant-prediction warnings;
  - the AUC gap between train and out-of-sample;
  - calibration;
  - per-fold feature importance.
- **Mandatory baselines on every run:** the base rate, logistic regression and the
  strategy's own rule (for example S2's signal).
- **Ensembling and seed averaging** are standard options, not ad hoc code.

### 6. Evaluation and trading (`pipeline/evaluate`)
- **One trading simulator from predictions** to positions to fills: bid/ask spread,
  slippage, swap, sessions and half-days, sizing, and marked equity.
- **Metrics:**
  - Sharpe, t, PSR and the **deflated Sharpe** (using the trial count from the ledger);
  - per-year and per-regime results;
  - turnover and costs;
  - bootstrap confidence intervals;
  - a **label-shuffle null**, retraining on shuffled labels for the luck distribution.
- **Prop-firm lifecycle** (FTMO or The5ers rules) as an optional final stage.

### 7. Experiment ledger (`pipeline/ledger`)
- **Every run appends one row:** experiment id, plan hash, code commit, data and feature
  hashes, config, splits, metrics, and whether the vault was touched. It is stored as
  SQLite plus JSON, small and local.
- **The ledger's run count feeds the deflated Sharpe.** Results from an uncommitted code
  state are marked "dirty" and cannot unseal the vault.

### 8. Deployment and parity (`pipeline/deploy`)
- **Python live bot** through the MetaTrader5 API, sharing the feature code exactly. Or an
  MQL5 port for simple rule strategies, as S2 has today.
- **Parity test:** replay recorded live bars through the backtest features. Values must
  match exactly, and fills are reconciled as `scripts/s2_reconcile.py` does.

### 9. Compute
- **Local first.** Today's datasets (≈ 80k rows) train in seconds.
- **A job runner** with the same interface locally, on the WRDS cloud (for TAQ-scale
  extraction) or on rented CPU.
- **GPUs** only for sequence or deep models on TAQ-scale data, and only after a cheaper
  model shows signal.

## Build order

1. **Core:** `known_at` normalisation, the calendar, the `known_at` accessor, the feature
   registry with the automatic leakage tests, labels with derived purge, the splitter
   catalogue (walk-forward, purged K-fold, CPCV), the vault and the ledger.
2. **Evaluation:** move the existing trading simulator and metrics under the pipeline,
   adding the deflated Sharpe from the ledger and the label-shuffle null.
3. **Re-express S2 and the ML study on it.** They must reproduce the recorded results
   (S2 holdout t 3.84; the clean ML walk-forward), which proves the pipeline is faithful.
4. **First new study:** the S2 dealer-gamma regime (WRDS Cboe Hanweck options data).
5. **Then:** RavenPack news and the TAQ factor zoo (the compute decision comes here).

## Definition of done for the core

- The three historical bugs, re-introduced on purpose, are each caught by an automatic
  check:
  - a one-hour clock shift by the clock gate;
  - bar-start reads by the perturbation test;
  - tiny weights by the weight check and the no-split warning.
- A study cannot read a sealed range without a committed plan, and the attempt is logged.
- S2's recorded holdout result reproduces exactly through the new pipeline.
