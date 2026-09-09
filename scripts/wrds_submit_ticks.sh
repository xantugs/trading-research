#!/bin/bash
# Raw TAQ trades + complete NBBO quotes for QQQ, 2018-2022, one task per month.
#
# Full daily table: pre-market, regular session and after-hours. Weekends and
# holidays need no handling - no table exists for them, so the extractor records
# them under manifest.json "missing_tables" and moves on.
#
# Output is large (tens of GB); it must go to scratch, not the home directory.
# Check the path first, then submit from ~/orb_taq:
#
#   ls -d /scratch/*/hantu
#   qsub -v ORB_TICK_ROOT=/scratch/cuhkcn/hantu/orb_ticks wrds_submit_ticks.sh
#
#$ -cwd
#$ -j y
#$ -N qqq_ticks
#$ -t 1-60
#$ -tc 3

set -euo pipefail

task="${SGE_TASK_ID:?This script must be submitted with qsub}"
idx=$(( task - 1 ))
year=$(( 2018 + idx / 12 ))
month=$(( idx % 12 + 1 ))
start="$(printf '%04d-%02d-01' "${year}" "${month}")"
end="$(date -u -d "${start} + 1 month" +%F)"

output_root="${ORB_TICK_ROOT:-/scratch/cuhkcn/hantu/orb_ticks}"
parent="$(dirname "${output_root}")"
if [ ! -d "${parent}" ]; then
  echo "Output parent ${parent} does not exist. Run 'ls -d /scratch/*/hantu' and" >&2
  echo "resubmit with -v ORB_TICK_ROOT=<correct path>/orb_ticks" >&2
  exit 1
fi

echo "Task ${task}: ${start} to ${end} (exclusive) -> ${output_root}/${year}/${start:0:7}"

python3 wrds_extract_taq.py \
  --symbol QQQ \
  --from "${start}" \
  --to "${end}" \
  --start-time 00:00:00 \
  --end-time 24:00:00 \
  --kind both \
  --format parquet \
  --output-dir "${output_root}/${year}/${start:0:7}"
