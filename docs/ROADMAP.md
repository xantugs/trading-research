# Research and engineering roadmap

## Release 1.4 readiness

- Project packaging, one-command setup/pipeline, strict configuration.
- Cached Dukascopy M1 download and optional streaming tick archive.
- Data-clock conversion, validation, missing-range diagnostics and manifests.
- CSV/JSON/offline-HTML reports; Streamlit data and upload fixes.
- Expanded regression tests, CI, compiler helper and stricter reconciliation.
- EA compiles locally; visual broker smoke test still pending.
- Live Dukascopy verification pending after the feed timeout on this machine.
- WRDS TAQ raw extraction and cloud-side M1 aggregation validated on QQQ.
- Experimental average-M1 range box, multi-candle confirmation, midpoint stop,
  prior-range proportion filter and lagged normalized MA slope filter in Python.
- Python experiment controls are pending MT5 EA parity and broker reconciliation.

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

Finish the 2012–2024 QQQ M1 history with the 07:30 New York start, validate all
year manifests and preserve a small raw-event fixture that redistribution terms
allow. Add a tick-driven replay layer for selected raw TAQ dates. Keep QQQ equity
results separately labeled from Dukascopy CFD and MT5 broker experiments.

## Deliberate later features

- Exact tick-driven execution and MT5 custom-tick export.
- MT5 EA parity for the experimental Python strategy switches.
- Multi-candle opening ranges and venue/session calendars.
- Spread guard, partial targets and explicit netting/margin rules.
- Intratrade equity and consistent daily-return performance statistics.
- Parameter sweeps with holdout periods; trade-sequence bootstrap/Monte Carlo.
- Broker-specific overnight financing and currency conversion.

These are planned features, not implemented switches.
