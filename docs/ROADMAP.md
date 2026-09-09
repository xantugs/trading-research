# Research and engineering roadmap

## Release 1.5 readiness

- Project packaging, one-command setup/pipeline, strict configuration.
- Cached Dukascopy M1 download and optional streaming tick archive.
- Data-clock conversion, validation, missing-range diagnostics and manifests.
- CSV/JSON/offline-HTML reports; Streamlit data and upload fixes.
- Expanded regression tests, CI, compiler helper and stricter reconciliation.
- EA compiles locally; visual broker smoke test still pending.
- Live Dukascopy verification pending after the feed timeout on this machine.
- WRDS TAQ raw extraction and cloud-side M1 aggregation validated on QQQ.
- Historical/adaptive opening-range forecasts, multi-candle confirmation,
  midpoint stop, range-proportion and lagged MA-slope filters in Python and EA v1.40.
- Decision audit trail, MFE/MAE, tick overlays, data-quality workspace,
  bootstrap, local stability grid and expanding yearly walk-forward.
- MT5 rule parity compiles locally; broker real-tick reconciliation remains pending.

## Next evidence to collect

1. Download a short real sample, verify the price scale and DST alignment.
2. Run one month in the MT5 visual tester and hand-check ten trades.
3. Fetch the same broker data and reconcile with management off.
4. Add commission and realistic spread/slippage sensitivities; compare EOD.
5. Reserve 2022 as a separate regime check, compare other index CFDs, and
   inspect broad parameter plateaus. Use an untouched forward period.
6. Record sample definitions and costs before comparing results; preserve each
   manifest and avoid selecting the best parameters on the final test period.

## WRDS next

Download and validate the completed 2012–2024 QQQ M1 history and the 2018–2022
raw archive. M1 runs now use whole shares, recorded spread and a gross-notional
cap. Next, build a lazy per-day NBBO quote store with nanosecond ordering and an
explicit New-York-to-strategy-clock conversion. Use the exported
`same_bar_ambiguous` timestamps to measure actual SL-first/TP-first ordering,
then expand exact quote replay to entries and all exits. Keep QQQ equity results
separately labeled from Dukascopy CFD and MT5 broker experiments.

## Deliberate later features

- Exact tick-driven execution and MT5 custom-tick export.
- Multi-candle opening ranges and venue/session calendars.
- Partial targets and explicit netting-account rules.
- Intratrade equity and consistent daily-return performance statistics.
- Nested multi-parameter optimization with an untouched final holdout.
- Broker-specific overnight financing and currency conversion.

These are planned features, not implemented switches.
