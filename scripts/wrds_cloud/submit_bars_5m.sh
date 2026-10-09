#!/bin/bash
# Run ON WRDS Cloud from ~/intraday_b:  qsub submit_bars_5m.sh
# One task per month, 2012-01 .. 2024-12 (the TAQ years this subscription can read), 4 at a time.
#$ -cwd
#$ -j y
#$ -N b_bars5m
#$ -t 1-156
#$ -tc 4
set -euo pipefail
idx=$(( SGE_TASK_ID - 1 ))
month=$(printf '%04d-%02d' $(( 2012 + idx / 12 )) $(( idx % 12 + 1 )))
export OMP_NUM_THREADS=1 ARROW_NUM_THREADS=1
python3 -c "import pyarrow" >/dev/null
python3 taq_bars_5m.py --month "${month}" --out /scratch/cuhkcn/hantu/intraday_b/bars
