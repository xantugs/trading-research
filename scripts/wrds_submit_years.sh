#!/bin/bash
#$ -cwd
#$ -j y
#$ -N orb_qqq_m1
#$ -t 2012-2024
#$ -tc 3

set -euo pipefail

year="${SGE_TASK_ID:?This script must be submitted with qsub}"
next_year="$((year + 1))"
output_root="${ORB_OUTPUT_ROOT:-orb_m1_years}"

python3 wrds_build_m1.py \
  --symbol QQQ \
  --from "${year}-01-01" \
  --to "${next_year}-01-01" \
  --output-dir "${output_root}/${year}"
