# Data contract and sources

## Canonical bars

CSV columns: `time,open,high,low,close,tick_volume,spread`. The first column is
the bar-open timestamp in an explicitly chosen, timezone-naive strategy clock.
`spread` is in **points**, multiplied by Params.point when use_data_spread is
true. Params.spread is instead in **price units**. Optional real_volume is
accepted but not used. Missing volume/spread columns become zero; explicitly
choose a spread assumption when the source does not provide one.

The loader also accepts MetaTrader's tab-separated export, including its
`<DATE>` / `<TIME>` columns. Invalid OHLC, nonfinite prices, negative volume
or spread, timezone-aware timestamps and duplicate bar timestamps are rejected.
It sorts valid unique timestamps but never forward-fills prices.

The command pipeline requires M1 data, builds midnight-aligned signal bars, and
refuses incomplete opening-range candles. Other partial bars and gaps are
reported, not silently filled. Scheduled exchange closures and data outages
cannot be distinguished without a venue calendar; inspect the quality report
and downloaded absent-day list. Entirely absent days do not generate trades.

## Dukascopy

The default source is
`https://datafeed.dukascopy.com/datafeed/SYMBOL/YYYY/MM/DD/BID_candles_min_1.bi5`,
with **zero-based month**. Day candle records are big-endian seconds/open/close/
low/high/float-volume, LZMA compressed. Tick records are milliseconds/ask/bid/
ask-volume/bid-volume. These are feed implementation details, not a guaranteed
versioned public API. The downloader fails on malformed or truncated records.

The instrument name and available minute history are also documented by the
independent downloader author:
[US 100 Tech metadata](https://www.dukascopy-node.app/instrument/usatechidxusd).
Our Python downloader does not depend on or copy that Node implementation.
Explicit divisors are configured for the listed index CFDs and selected FX
pairs. Unknown instruments require `--scale`; verify a day's price level
against the provider before a large experiment.

Native candle volume is a **liquidity proxy**, not exchange volume or MT5 tick
count. Zero-volume candles are retained. A volume spike may help diagnose a
clock problem but does not prove which candle is the cash open. MT5 custom
import files contain one synthetic tick count per M1 bar; disable the volume
filter for cross-engine reconciliation of these imports.

The default spread is a user-specified constant. Subtracting asynchronous ask
and bid candle closes does not measure the executable spread at one instant;
the project does not manufacture a spread column from that difference.
Raw ticks retain synchronized bid/ask fields for future tick-engine work.

Downloads include every requested calendar day (Sunday data included).
HTTP 404/empty responses are listed in metadata; they are not cached as
permanent absence. Other network failures abort. Successful artifacts are
cached and decompressed again on reuse to detect corruption. An invalid cached
artifact should be removed from the local cache and fetched again.
`--to` is exclusive and must be no later than today UTC.

`python run_all.py --source dukascopy` defaults to USATECHIDXUSD from
2022-01-01 through the latest completed UTC day. `--data-only` installs project
dependencies and prepares/validates the files without running a report or app.
On an identical repeat, M1, M15 and MT5 import hashes are checked against the
dataset metadata before local reuse. `--refresh-data` bypasses that reuse while
retaining the raw BI5 cache.

## Clock choices

- `ny+7`: convert UTC to America/New_York, remove timezone, then add seven
  wall-clock hours. This keeps the US cash open at 16:30 all year.
- `utc`: retain UTC. The NY open changes between 13:30 and 14:30.
- `+3`: fixed UTC+3. The NY open changes between 16:30 and 17:30.

Fall-back DST can create duplicate naive timestamps in markets trading through
that transition; the bar loader rejects them instead of choosing a price.
For such markets use UTC or a fixed clock and a suitable session model.
The current ORB engine uses one fixed time per day, so a dynamic session
calendar would be a future extension.

MT5's Python API documents returned bar/tick timestamps as UTC:
[MetaQuotes copy_rates_range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py).
The terminal fetcher therefore requires `--tz` explicitly. Compare an exported
timestamp with the broker chart and the EA log; broker/server conventions
must be confirmed empirically before reconciliation. Do not double-convert
an export that is already in broker server time.

## Keeping data out of Git

Downloaded files and generated reports remain local. Dataset manifests include
provider, date bounds, clock, scale, spread assumption, volume semantics, missing
dates and the M1 checksum. Result manifests capture the normalized source hash.
Check the provider's applicable access and redistribution terms before sharing
market data; this repository supplies software, not a data redistribution grant.

## WRDS NYSE TAQ

The WRDS Cloud scripts use the licensed NYSE TAQ Millisecond Daily Product for
QQQ. `wrds_build_m1.py` queries daily consolidated NBBO and trade tables on the
WRDS compute nodes and transfers only compact M1 output. OHLC comes from valid,
non-crossed best bids. Spread is the within-minute 95th percentile NBBO spread,
rounded up to symbol points. `tick_volume` counts positive consolidated trades
with correction code `00`; `real_volume` sums their share size.

That p95 spread is a conservative quote-update statistic for the whole minute.
Using it at the minute open therefore looks ahead within that minute and is not
the exact executable spread at entry. It is suitable as the first M1 cost proxy;
ordered NBBO ticks are still required to validate fills and same-bar ordering.

TAQ source timestamps are New York wall time. The builder adds seven hours so
09:30 is stored as 16:30 throughout the year, matching the project strategy
clock. Its 07:30 source start provides two full hours before the opening range.
Gzip CSV input is accepted directly by the loader and ORB Lab.

The separate raw extractor writes consolidated trades and complete NBBO quote
updates by day. These are event-level SIP records, not full depth-of-book data,
and they are not broker CFD ticks. The overlay preserves nanosecond ordering,
reports missing/crossed quotes and applies the explicit +7-hour clock shift;
future execution replay must reject unusable quotes rather than silently use
them. The current Python engine remains M1-driven.
See [the WRDS workflow](WRDS.md).
