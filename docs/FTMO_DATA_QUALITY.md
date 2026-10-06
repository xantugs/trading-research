# FTMO-Demo XAUUSD data inventory

Snapshot verified on 17 September 2026. The active MT5 account reported
`FTMO Global Markets Ltd / FTMO-Demo`; the exact symbol was `XAUUSD`, a USD
spot CFD with a 100 oz contract, 0.01 price point and 0.01-lot minimum/step.
The downloader rejects a different server or symbol specification. MT5 Python
timestamps are stored as delivered and labelled UTC. **They are not UTC**: the
values carry the FTMO-Demo server clock, which runs at New York + 7 h in every
season (every 08:30 New York release prints at 15:30 in the stored clock; see
`docs/RESEARCH_V2.md`, "Broker clock", and `scripts/verify_broker_clock.py`).
The `broker_clock` configuration key controls decoding at load time; the
default `stored_as_utc` preserves the historical interpretation used by every
result before 18 September 2026, and `new_york_plus_7` decodes the true time.
The "UTC coverage" bounds in the table below are stored-clock bounds.

| Layer | UTC coverage | Records | Local storage |
| --- | --- | ---: | ---: |
| M1 Bid OHLC, tick volume and recorded spread | 2018-01-02 through 2026-09-16 | 3,053,966 bars in 105 month files | 69.2 MB |
| Native bid/ask ticks | 2021-10-04 through 2026-09-16 | 200,890,543 ticks in 1,278 nonempty day files | 1.68 GB |

The M1 cache is `C:\data\ftmo_xauusd_m1\FTMO-Demo\XAUUSD`; the tick cache is
`C:\data\ftmo_xauusd_ticks\FTMO-Demo\XAUUSD`. Both are outside OneDrive and
Git. `source.json`, per-partition sidecars, and `download_manifest.json` record
the broker identity, requested UTC windows, counts and date bounds. The
verifiers reopen every Parquet file and write machine-readable quality reports
under `outputs/`.

The M1 verifier flagged 24 weekdays without bars for calendar review. It also
found 35,087 bars with zero recorded spread. Four months exceed 5% zero-spread
bars: April 2021 (5.6%), May 2021 (25.9%), June 2021 (18.8%), and September
2021 (55.9%). The bar backtester uses its configured fallback spread on those
bars. That is an assumption, not recovered historical Ask data. The broker
serves M1 before October 2021 but did not return native ticks for those earlier
years in the coverage probe.

The tick verifier found one complete M1 trading day with no native ticks:
**2021-12-01**. Across the tick-covered dates it also found 3,064 M1 minutes
without a corresponding tick minute, including 548 minutes during the configured
09:30–16:00 New York session. There are 459 tick minutes without a matching M1
bar. Large individual gaps are often outside that session, but no tick-based
replay should silently fill any missing quote path. The verifier records each
affected date; a future tick engine should skip or explicitly flag affected
sessions. Some gaps reach the opening range itself, including 09:44 New York
on 30 July 2024 and 09:50 on 31 July 2024. Same-millisecond tick records
(1,325,339 adjacent pairs) retain their
original order.

The 2025-onward data is reserved as the final holdout. Acquisition and quality
checks counted files and gaps, but no strategy result was computed from it.
The current research engine uses M1 bars for simulated fills; this tick archive
is available for later execution validation. The archive does not prove that
historical FTMO-Demo prices match a future challenge or funded account's fills.

From the repository root, refresh the local cache and quality reports with:

```powershell
python run_all.py ftmo-data --with-ticks
```

Completed periods are reused. The default exclusive end date is the current
UTC date, so a partial current day is not archived. Raw broker data and local
quality reports are excluded from GitHub.
