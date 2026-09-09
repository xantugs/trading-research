# WRDS TAQ workflow

The WRDS adapter targets **QQQ in NYSE TAQ Millisecond Daily Product**. It keeps
two distinct data layers:

- yearly M1 research bars built on WRDS Cloud from valid non-crossed consolidated
  NBBO quotes, with consolidated trade counts and share volume;
- raw daily trades and complete NBBO quotes for selected periods that need
  event-level execution analysis.

M1 is the practical full-history layer. It does not contain ticks. Raw exports
retain the detailed SIP events but still do not provide full order-book depth,
queue position, or a broker CFD price.

## Build yearly M1 files on WRDS Cloud

Upload these three files into `~/orb_taq`:

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
to the SGE output log. Tables before 2018 do not expose `time_m_nano`; the builder
detects this and falls back to `time_m` ordering.

The current builder begins at **07:30 New York time**. This supplies the full two
hours before the 09:30 opening range. Earlier files built with the old 08:30
default remain valid for the 50-M1 average-box experiment, but the engine will
reject their two-hour range-ratio filter as insufficient history.

## Raw tick exports

`wrds_submit_ticks.sh` submits one array task per month over 2018-2022 and calls
`wrds_extract_taq.py` with a full 24-hour window, so pre-market and after-hours
events are included. Weekends and holidays have no TAQ table; they are recorded
under `manifest.json` -> `missing_tables` rather than failing the task.

The extractor writes **zstd Parquet** by default, which runs roughly 3-4x smaller
than gzip CSV on this data because `sym_root` is one repeated value, `date` is
constant per file and timestamps are monotonic. `--format csv` restores the old
gzip CSV artifacts. Parquet needs `pyarrow` on the compute node; the job aborts
with a clear message if it is unavailable.

Raw output is tens of GB and must not go to the home directory:

```sh
ls -d /scratch/*/hantu || mkdir -p /scratch/<institution>/<user>/orb_ticks
qsub -v ORB_TICK_ROOT=/scratch/<institution>/<user>/orb_ticks wrds_submit_ticks.sh
```

Run `wrds_probe_ticks.sh` first to measure both formats on six sample days,
including 2020-03-16 as the worst case, before committing sixty tasks.

Scratch is staging, not storage: it is purged periodically. Prefer analysing the
ticks on WRDS Cloud and retrieving aggregates over moving the whole set.

## Download and run locally

Create `data/wrds/years`, connect with SFTP, then download `orb_m1_years_0730` into
that folder. WRDS password and MFA stay in the interactive SFTP client and are
never stored by this project.

Prepare one merged local gzip file, validate it, run the discussion setup, and
write a report:

```sh
python run_all.py --source wrds --bars data/wrds/years --symbol QQQ \
  --config configs/discussion.json --no-app
```

Omit `--no-app` to open ORB Lab. `run_all.py` accepts one WRDS M1 file or a
directory of non-overlapping yearly files. Directory input is combined under
`data/wrds/prepared/`; overlapping timestamps are rejected.

To test another average-candle width, pass any positive decimal multiplier:

```sh
--set range_avg_multiplier=2.35
```

Use a fresh `--output` directory for every saved experiment.

## Export raw event data for exact replay work

For a bounded date range on WRDS Cloud:

```sh
python3 wrds_extract_taq.py --symbol QQQ --from 2024-03-08 --to 2024-03-12 \
  --output-dir orb_exports_2024
```

This writes one trade file and one complete-NBBO quote file per available day.
Keep these provider files outside Git. A future tick engine will consume the raw
layer; the current backtest engine resolves entries and exits on M1 bars.
