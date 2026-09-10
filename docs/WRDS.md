# WRDS TAQ workflow

The WRDS adapter targets **QQQ in NYSE TAQ Millisecond Daily Product**. The
licensed data stays outside Git in two independent local roots:

- `C:\data\wrds_m1`: yearly M1 research bars built on WRDS Cloud;
- `C:\data\wrds_ticks`: raw daily consolidated trades and complete NBBO quotes.

The roots are different research inputs. M1 mode never reads the tick root.
Tick mode never reads or falls back to the M1 root; it derives all of its M1
bars and default M15 signal candles from the raw-event tree.

## Current local inventory

The M1 root contains 13 yearly QQQ gzip files covering 2012–2024. The raw root
contains QQQ for 2018–2022: 2,518 Parquet files for 1,259 trading days, paired
as one consolidated-trade file and one complete-NBBO file per day, plus 60
monthly manifests. The 92 missing-table entries represent 46 market holidays,
with one absent trade table and one absent quote table per holiday.

The raw archive is event-level SIP data. It retains New York timestamps and
subsecond ordering, but it is top of book rather than full order-book depth and
is not a broker CFD price feed.

## Bar semantics

Both cloud and local builders apply the same definition:

- OHLC is the first, maximum, minimum, and last valid complete-NBBO `best_bid`
  in each minute, with first/last ordered by `time_m` plus nanoseconds when that
  field exists;
- valid quotes have positive finite bid/ask and `best_ask >= best_bid`;
- `tick_volume` is the number of positive consolidated trades whose correction
  code is `00`;
- `real_volume` is the sum of share size for those eligible trades;
- `quote_updates` is retained separately;
- `spread` is the within-minute p95 NBBO spread, rounded up to $0.01 points;
  raw p50 and p95 spread diagnostics are retained by the local builder.

TAQ timestamps are New York wall time. The builders add seven hours, so 09:30
New York is stored as 16:30 throughout the year. The M1 spread summarizes the
entire minute and therefore has within-minute look-ahead; it is a conservative
bar-model cost proxy, not an entry-time quote.

## Build yearly M1 files on WRDS Cloud

Upload these files into `~/orb_taq`:

```text
scripts/wrds_build_m1.py
scripts/wrds_extract_taq.py
scripts/wrds_submit_years.sh
```

From the WRDS shell:

```sh
cd ~/orb_taq
qsub -v ORB_OUTPUT_ROOT=orb_m1_years_0730 -t 2012-2024 wrds_submit_years.sh
qstat
```

The submit script limits the array to three concurrent years. Each task writes
one gzip CSV and manifest under `orb_m1_years_0730/YEAR/`. Progress is written
to the SGE output log. Tables before 2018 do not expose `time_m_nano`; the
builder detects this and falls back to `time_m` ordering.

The builder now covers **04:00 to 20:00 New York time** - pre-market open through
after-hours close, every minute US equities quote. Earlier exports were narrower
(07:30-16:01, and before that 08:30) and are **not interchangeable**: an 08:30
file has one fewer hour per day than a 07:30 file, which shows up as roughly 60
missing M1 bars per trading day and silently starves any filter that needs a
two-hour pre-open lookback.

`wrds_submit_years.sh` passes the window explicitly, so the WRDS Cloud export and
the local `wrds_build_m1_local.py` rebuild cannot drift apart. Override both with
`ORB_SESSION_START` / `ORB_SESSION_END`, or `--start-time` / `--end-time` when
calling a builder directly.

**A year exported under an older window must be rebuilt**, not mixed in. Compare
`source_start_time` in each year's `manifest.json` before trusting a set.

## Export raw ticks on WRDS Cloud

`wrds_submit_ticks.sh` submits one array task per month and calls
`wrds_extract_taq.py` for every available daily table. The completed 2018–2022
pull includes full-day trades and quotes. Its exclusive 23:59:59 endpoint omitted
no QQQ events in the final second; new runs use the corrected 24:00 endpoint.
Weekends and holidays have no table and are recorded in each manifest rather
than treated as job failures.

The extractor writes zstd Parquet by default. Raw output is tens of gigabytes
and belongs on scratch while a cloud job is running:

```sh
ls -d /scratch/*/hantu || mkdir -p /scratch/<institution>/<user>/orb_ticks
qsub -v ORB_TICK_ROOT=/scratch/<institution>/<user>/orb_ticks wrds_submit_ticks.sh
```

Run `wrds_probe_ticks.sh` first when measuring a new date range. Scratch is
temporary; verify local copies with `scripts/verify_tick_download.py` before
removing the staged archives. WRDS credentials stay in the interactive SSH,
SFTP, or Jupyter session and are never stored by this project.

## Run the two local modes

Run the complete M1 research layer and finish after writing reports:

```powershell
python run_all.py --mode m1 --no-app
```

This reads only `C:\data\wrds_m1`. A different M1 file or non-overlapping yearly
directory can be supplied with `--bars`, but `--tick-root` is rejected in this
mode. Sibling manifests are validated when present; no manifests produces a
provenance warning, while a partial manifest set is rejected.

Run the full available raw-event period:

```powershell
python run_all.py --mode tick --from 2018-01-01 --to 2023-01-01 --no-app
```

For a short end-to-end check:

```powershell
python run_all.py --mode tick --from 2018-01-01 --to 2018-02-01 --no-app
```

Tick mode reads only `C:\data\wrds_ticks`; `--bars` is rejected. Use
`--tick-root PATH` to select another raw tree. It creates a content-keyed M1
cache under the project `.cache/wrds_tick_m1/`, resamples the selected signal
timeframe (M15 by default), and then replays the same root's ordered NBBO for
execution. The cache does not import, consult, or update `C:\data\wrds_m1`.
Omit `--no-app` from either command to open ORB Lab.

Both modes select `configs/qqq.json` unless `--config` is given. That profile
uses whole shares, $1 P/L per $1 share move, a declared per-share commission,
recorded spread where applicable, and a 1× gross-notional cap. Commission and
leverage remain assumptions to test.

## Incomplete opening ranges

The M1 pipeline excludes five dates because the 16:30–16:44 strategy-clock
opening range has fewer than 15 bars:

- `2015-03-31`: 11/15 bars; raw ticks are outside the local archive, so the
  cause is an unconfirmed valid-NBBO/source gap;
- `2018-11-16`: 4/15 bars; raw quote rows continue during the rest of the
  opening-range window, but none form a valid non-crossed NBBO;
- `2020-03-09`: 5/15 bars, matching the market-wide opening circuit breaker;
- `2020-03-12`: 6/15 bars, matching the market-wide opening circuit breaker;
- `2020-03-16`: 1/15 bar, matching the market-wide opening circuit breaker.

The pipeline excludes the entire affected date. It does not forward-fill or
manufacture the missing part of an opening range.

## Strict tick milestone

Tick mode uses the bar-derived strategy signals, then chooses observed
executable-side quotes from nanosecond-ordered valid complete NBBO: ask for a
long entry or short exit, and bid for a short entry or long exit. It supports
close-confirmed entries and fixed stop, target, and EOD exits.

This milestone does not support immediate entry, breakeven, or trailing. An
observed top-of-book quote resolves event order and displayed price, but does
not model depth, queue position, routing delay beyond the configured latency,
partial fills, hidden liquidity, or market impact. It is a TAQ execution model,
not a promise of a broker fill.

## Standalone local bar build

The same self-contained raw tree can be converted explicitly when inspecting
builder parity:

```bat
.venv\Scripts\python scripts\wrds_build_m1_local.py C:\data\wrds_ticks ^
  --output-dir C:\data\wrds_tick_bars
```

This writes reproducible monthly and yearly M1 files plus manifests. The normal
tick-mode command performs its own cached build, so this standalone output is
not required and is not a fallback for either execution mode.

For a bounded cloud export outside the completed archive:

```sh
python3 wrds_extract_taq.py --symbol QQQ --from 2024-03-08 --to 2024-03-12 \
  --output-dir orb_exports_2024
```

Keep all provider files outside Git and follow the applicable WRDS and NYSE
redistribution terms.
