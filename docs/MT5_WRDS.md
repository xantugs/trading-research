# WRDS QQQ ticks in MetaTrader 5

## What the pipeline does

`scripts/wrds_to_mt5_ticks.py` reads the local daily QQQ complete-NBBO Parquet
files and emits a compact daily binary read by `Import_WRDS_Ticks.mq5`:

```text
32-byte header: magic, format version, price digits, source-day seconds, row count
12-byte record: milliseconds since midnight, bid price points, ask price points
```

The date interval is New York wall time, start-inclusive and end-exclusive.
Every source time from 00:00 through the next 00:00 is eligible. There is no
regular-session, premarket or after-hours filter.

Invalid timestamps, nonpositive prices and crossed markets are removed.
Consecutive rows whose bid and ask are unchanged are removed because only TAQ
metadata or displayed size changed. Every bid/ask change remains. Nanosecond
order is retained when MT5's millisecond timestamps collide.

Each binary has a sibling manifest with source counts, cleaning counts, time bounds,
same-millisecond counts, size and SHA-256. `verify_mt5_ticks.py` independently
streams the file and checks that contract.

MT5 exposes millisecond tick timestamps, so source nanoseconds are not written.
When several price changes fall in one millisecond, all remain as separate records
in their original WRDS order and receive the same MT5 `time_msc` value.
The export query orders rows by `time_m`, `time_m_nano`, and a quote sequence
column when the table provides one. If distinct states tie on the full timestamp
and no sequence column exists, their Parquet row order is preserved and the
manifest records the number of such transitions.

## Prepare data and binaries

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_mt5_wrds.ps1 `
  -From 2018-01-02 -To 2018-01-03
```

The wrapper installs only the `wrds` Python extra, compiles the EA and importer,
installs both binaries, converts the interval into MT5 `Common\Files`, and runs
the verifier. It refuses to replace existing files unless `-Overwrite` is given.

Use monthly ranges for the archive. A real 104-day sample shrank **669.9 MiB of
tab-delimited CSV to 178.6 MiB of binary**. The completed 2018–2022 archive is
**1,259 daily files, 632,204,742 ticks and 7,586,497,192 bytes (7.065 GiB)**.
The original local tree was 23.59 GiB because it also contained consolidated
trades. The full converter therefore works one month at a time and can remove
verified source months as it proceeds.

The final whole-archive structural and SHA-256 pass reported 1,259 files and zero
problems. Exactly one distinct-state transition in the archive tied on its full
WRDS timestamp without a sequence field; its source row order is preserved and
recorded in the relevant manifest.

To perform that conversion and verified source cleanup automatically:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\convert_all_wrds_to_mt5.ps1 `
  -DeleteSourceAfterVerify -Overwrite
```

The script refuses to delete a month until the converter succeeds, every daily
file and hash manifest passes the independent verifier, and the output filenames
exactly match the source quote dates.

## Import

In MT5, refresh Navigator and run `Import_WRDS_Ticks` under Scripts. Use an exact
daily filename first. After that, `QQQ_*_ticks.bin` imports every staged file.
The importer:

- creates or updates `QQQ.WRDS`;
- configures stock-style USD/whole-share properties;
- validates each daily binary header, size, prices and ordering again;
- imports one day at a time to bound memory use;
- replaces the entire corresponding custom-symbol day, preventing duplicates.

The custom-symbol contract is one lot per QQQ share, two digits, a $0.01 point
and tick size, a $0.01 tick value per lot, and a one-lot volume step. Quote and
trade sessions are enabled for the full weekday. Weekend tables are absent.

## Strategy Tester

Select `ORB_Backtest`, `QQQ.WRDS`, M15 and **Every tick based on real ticks**.
The history clock is New York wall time, so EA v1.50 defaults to 09:30 opening
range, 12:00 entry cutoff and 15:45 optional EOD close. Do not apply the old
`ny+7` shift or the 16:30/19:00 CFD settings.

For the first smoke test:

1. Import one day and confirm ticks exist in the custom-symbol history.
2. Import one month and run in visual mode.
3. Disable optional ATR, trend, breakeven and trailing rules.
4. Confirm the first eligible breakout is the day's only attempted direction.
5. Check ten trades against range, confirmation, next-candle entry, stop, target
   and exit rules.
6. Save the tester report, EA CSV log and exact input set.

`InpVolumeSpikeMin` defaults to zero. The old volume diagnostic cannot be compared
with this feed because metadata-only quote updates were removed and imported
quote ticks carry volume zero.

## Limits

This is complete NBBO top-of-book history rather than broker executions. It has
no depth, queue position, latency, routing, partial-fill or impact model. LAST
and VOLUME are zero because the active feed is quotes; consolidated trade files
are not mixed into the executable bid/ask path. Custom-symbol commissions are
not configured, so initial tester results are gross.

The first actual runtime call to `CustomTicksReplace` and a visual EA smoke test
must happen inside MT5. Compilation and file verification cannot substitute for
that terminal-level check.

WRDS data is licensed and is excluded from Git. GitHub receives the converter,
importer, manifests/tests built from synthetic fixtures, and documentation; it
does not receive real QQQ rows.
