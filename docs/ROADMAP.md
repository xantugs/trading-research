# Research and engineering roadmap

## Release 1.3 readiness

- Project packaging, one-command setup/pipeline, strict configuration.
- Cached Dukascopy M1 download and optional streaming tick archive.
- Data-clock conversion, validation, missing-range diagnostics and manifests.
- CSV/JSON/offline-HTML reports; Streamlit data and upload fixes.
- Expanded regression tests, CI, compiler helper and stricter reconciliation.
- EA compiles locally; visual broker smoke test still pending.
- Live Dukascopy verification pending after the feed timeout on this machine.

## Next evidence to collect

1. Download a short real sample, verify the price scale and DST alignment.
2. Run one month in the MT5 visual tester and hand-check ten trades.
3. Fetch the same broker data and reconcile with management off.
4. Add commission and realistic spread/slippage sensitivities; compare EOD.
5. Reserve 2022 as a separate regime check, compare other index CFDs, and
   inspect broad parameter plateaus. Use an untouched forward period.
6. Record sample definitions and costs before comparing results; preserve each
   manifest and avoid selecting the best parameters on the final test period.

## WRDS after approval

Identify actual licensed datasets and whether intraday coverage matches this
strategy. Add a separate adapter with a normalized UTC staging table,
timezone/adjustment metadata, entitlement-aware configuration and offline
contract tests. Keep credentials outside Git. CFD and equity/futures research
should remain separately labeled rather than splicing unlike prices together.

## Deliberate later features

- Exact tick-driven execution and MT5 custom-tick export.
- Multi-candle opening ranges and venue/session calendars.
- Spread guard, partial targets and explicit netting/margin rules.
- Intratrade equity and consistent daily-return performance statistics.
- Parameter sweeps with holdout periods; trade-sequence bootstrap/Monte Carlo.
- Broker-specific overnight financing and currency conversion.

These are planned features, not implemented switches.
