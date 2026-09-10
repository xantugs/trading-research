# Data contract and sources

> This document describes the retained Python/M1 research tooling. The active
> pipeline converts full-day WRDS NBBO events directly to MT5 ticks; see
> [MT5_WRDS.md](MT5_WRDS.md).

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

M1 mode loads these bars and builds midnight-aligned signal bars. Tick mode
derives the same M1 contract from its own raw events before building M15 signal
candles. A date whose opening-range candle is incomplete is excluded from the
strategy input. Other partial bars and gaps are reported, and prices are never
forward-filled. Entirely absent days do not generate trades.

## Dukascopy

The Dukascopy M1 endpoint is
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
Raw Dukascopy ticks retain synchronized bid/ask fields, but the WRDS tick mode
does not read this layout and there is no Dukascopy tick executor yet.

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
tick mode instead replays ordered NBBO quotes for executable-side fills.

TAQ source timestamps are New York wall time. The builder adds seven hours so
09:30 is stored as 16:30 throughout the year, matching the project strategy
clock. Its 07:30 source start provides two full hours before the opening range.
Gzip CSV input is accepted directly by the retained Python research loader.

The separate raw extractor writes consolidated trades and complete NBBO quote
updates by day. These are event-level SIP records, not full depth-of-book data,
and they are not broker CFD ticks. Nanosecond ordering is preserved. Invalid,
crossed or incomplete quotes are rejected, and the explicit +7-hour
clock shift is applied before strategy evaluation.

The runner has two deliberately non-overlapping WRDS modes:

```powershell
python run_all.py --mode m1 --no-app
python run_all.py --mode tick --from 2018-01-01 --to 2023-01-01 --no-app
```

M1 mode reads only `C:\data\wrds_m1` by default. Tick mode reads only
`C:\data\wrds_ticks`, derives its own M1 and default M15 signal candles, and
replays ordered top-of-book NBBO quotes for execution. `--bars` can replace the
M1 root only in M1 mode; `--tick-root` can replace the raw root only in tick
mode. Neither mode falls back to or combines with the other root.

The local real-data inventory is QQQ M1 for 2012–2024 and QQQ raw trades plus
complete NBBO for 2018–2022. The raw tree contains 2,518 Parquet files for 1,259
trading days and 60 monthly manifests. Five dates are excluded from M1 research
because their 16:30–16:44 strategy-clock opening range is incomplete:
`2015-03-31`, `2018-11-16`, `2020-03-09`, `2020-03-12`, and `2020-03-16`.
The three 2020 gaps coincide with market-wide opening circuit-breaker halts.
During the rest of the 2018 opening-range window, the raw file has quote rows
but no valid non-crossed NBBO. Raw ticks do not cover 2015, so the earlier gap
cannot be diagnosed from the local event archive. Whole dates are excluded;
missing bars are not filled.

Strict tick execution currently supports close-confirmed entries and fixed
SL/TP/EOD exits. It does not yet support immediate entry, breakeven, or trailing.
The observed SIP top of book gives bid/ask prices and ordering, but not depth,
queue priority, broker routing, partial fills, or market impact.
See [the WRDS workflow](WRDS.md).
