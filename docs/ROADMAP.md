# Research and engineering roadmap

## Release 1.6 readiness

- QQQ is the primary research instrument, with WRDS M1 for 2012–2024 and raw
  trade/complete-NBBO events for 2018–2022 available locally.
- Strict M1 and tick modes use separate external roots and have no cross-root
  fallback or overlap.
- The tick root builds its own parity-checked M1 and default M15 signal candles;
  ordered top-of-book NBBO replays close-confirmed fixed-exit trades.
- Five incomplete opening-range dates are excluded and documented instead of
  being filled or silently shortened.
- Project packaging, one-command setup, strict configuration, reports, dark
  Streamlit workspace, tests, CI, compiler helper and reconciliation are in place.
- Historical/adaptive opening-range forecasts, multi-candle confirmation,
  midpoint stop, range-proportion and lagged MA-slope filters are implemented in
  Python and EA v1.40.
- EA v1.40 compiles locally; visual broker smoke testing and same-instrument
  reconciliation remain pending.

## Next evidence to collect

1. Run all 2018–2022 raw dates through strict tick mode and preserve the result
   manifest, execution audit, wall time and cache size.
2. Compare M1 and tick results over identical dates and settings. Separate
   signal differences from entry price, exit order and quote-coverage effects.
3. Measure how often `sl_first` changes the M1 outcome and replace that
   assumption with observed NBBO ordering where strict replay is supported.
4. Test commission, latency, delayed-quote and leverage sensitivities before
   interpreting any performance result.
5. Define development, validation and untouched holdout periods before changing
   strategy parameters; preserve every experiment manifest.
6. Check whether the intended MT5 broker lists QQQ. If it does, fetch matching
   broker data and reconcile with management off; if it does not, keep the TAQ
   equity model and CFD EA tests labeled as different instruments.

## WRDS follow-up

The data transfer, footer verification, cloud M1 build, local parity builder,
raw-derived signal path and milestone-one NBBO executor are complete. Remaining
work is empirical validation: complete-period replay, M1-versus-tick attribution,
coverage-delay distributions and cost assumptions. The current local M1 years
have no sibling manifests, so result provenance records that warning; a future
re-export can restore per-year source checksums without blocking research.

## Deliberate later features

- Immediate-entry, breakeven and trailing support in the strict tick executor.
- Marketable-order depth, queue, partial-fill and market-impact models where a
  suitable dataset and explicit assumptions exist.
- MT5 custom-tick export and broker-specific trigger semantics.
- Multi-candle opening ranges and venue/session calendars.
- Partial targets and explicit netting-account rules.
- Intratrade equity and consistent daily-return performance statistics.
- Nested multi-parameter optimization with an untouched final holdout.
- Broker-specific overnight financing and currency conversion.

These are planned features, not implemented switches.
