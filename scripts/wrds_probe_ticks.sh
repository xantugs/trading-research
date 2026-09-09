#!/bin/bash
# Measure real per-day tick export sizes, parquet vs gzip CSV, before
# committing to the multi-year pull. One task per sample day, full 24h window.
#
# Submit from ~/orb_taq:
#   python3 -c "import pyarrow; print(pyarrow.__version__)"
#   qsub wrds_probe_ticks.sh
# Then:
#   du -sh tick_probe/*/parquet tick_probe/*/csv
#$ -cwd
#$ -j y
#$ -N qqq_tick_probe
#$ -t 1-6
#$ -tc 3

set -euo pipefail

task="${SGE_TASK_ID:?This script must be submitted with qsub}"
days=(2018-06-15 2019-06-14 2020-03-16 2020-06-15 2021-06-15 2022-06-15)
day="${days[$((task - 1))]}"
next="$(date -u -d "${day} + 1 day" +%F)"

for fmt in parquet csv; do
  echo "=== ${day} ${fmt} ==="
  python3 wrds_extract_taq.py \
    --symbol QQQ \
    --from "${day}" \
    --to "${next}" \
    --start-time 00:00:00 \
    --end-time 24:00:00 \
    --format "${fmt}" \
    --output-dir "tick_probe/${day}/${fmt}"
done

du -sh "tick_probe/${day}/parquet" "tick_probe/${day}/csv"
