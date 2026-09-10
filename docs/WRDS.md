# WRDS TAQ data

## Local inventory

The active source is `C:\data\wrds_ticks`: QQQ consolidated trades and complete
NBBO quotes from 2018–2022. It contains 2,518 Parquet files for 1,259 trading
days, paired as one quote and one trade file per day, plus monthly manifests.
The 92 missing-table records are the two absent tables for each of 46 genuine
NYSE holidays.

The files retain New York timestamps and subsecond ordering. They are licensed
SIP data and stay outside Git. They represent consolidated top of book, not a
broker CFD and not full order-book depth.

## Active use

The project now uses complete NBBO quote events to build MT5 custom-symbol ticks.
It does not aggregate the active feed to M1 before backtesting. The converter
reads the entire New York calendar day:

```text
00:00:00.000 <= timestamp < next-day 00:00:00.000
```

No session window is applied. Empty overnight hours simply have no rows. See
[the MT5/WRDS runbook](MT5_WRDS.md) for conversion and import.

The local `C:\data\wrds_m1` collection and Python M1/tick execution modes remain
historical research tools. They are not inputs to the active MT5 workflow.

## Source extraction record

The cloud scripts remain under `scripts/` so the archive can be reproduced:

- `wrds_extract_taq.py` exports daily consolidated trades and complete NBBO;
- `wrds_submit_ticks.sh` submits monthly SGE array tasks;
- `wrds_probe_ticks.sh` sizes a proposed extraction;
- `wrds_watch_ticks.sh` reports durable progress;
- `verify_tick_download.py` opens each downloaded Parquet footer.

The completed extraction used the full day and Parquet with Zstandard level 9.
Scratch output was tarred per year, downloaded over parallel HTTPS, extracted to
`C:\data\wrds_ticks`, and verified before the remote tars were removed.

New cloud pulls must use an end-exclusive next-day boundary rather than
`23:59:59`; this guarantees that the final second and all fractional timestamps
are eligible.

Keep WRDS credentials in the interactive WRDS session. Never store credentials,
raw data, generated MT5 tick CSVs or provider manifests in this repository.
