#!/bin/bash
# Gate 2 of docs/JKX_REPLICATION_PLAN.md on the shared GPU server (run from /root/autodl-tmp/trading):
#   setsid nohup bash scripts/jkx_gate2.sh > logs/gate2.log 2>&1 &
# Each model's five seeds train side by side on one GPU (identical maths to training them one
# after another), then the ensemble predicts the 2001-2019 test years. Images stay in system
# RAM (--data-on-cpu) so the job needs only a few GB of GPU memory on the shared card.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=venv/bin/python
DATA=data
export OMP_NUM_THREADS=4

gpu_busy() {   # another process holding more than 2 GB of GPU memory
  local used
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
  [ "$used" -gt 2000 ]
}

for model in "20 20" "5 5" "20 5"; do
  set -- $model
  days=$1; horizon=$2; name="I${days}R${horizon}"
  if [ -f "$DATA/predictions/$name.parquet" ]; then echo "$name: done already"; continue; fi
  if gpu_busy; then echo "$(date) GPU in use by another job; stopping before $name"; nvidia-smi; exit 3; fi
  echo "$(date) $name: training seeds 0-4 in parallel"
  for seed in 0 1 2 3 4; do
    if [ -f "$DATA/models/${name}_seed${seed}.pt" ]; then continue; fi
    $PY -m scripts.jkx_train --days "$days" --horizon "$horizon" --data "$DATA" --device cuda --data-on-cpu \
      --stage train --seed-list "$seed" > "logs/${name}_seed${seed}.log" 2>&1 &
  done
  wait
  for seed in 0 1 2 3 4; do
    [ -f "$DATA/models/${name}_seed${seed}.pt" ] || { echo "$name seed $seed failed; see logs/${name}_seed${seed}.log"; exit 1; }
  done
  echo "$(date) $name: predicting"
  $PY -m scripts.jkx_train --days "$days" --horizon "$horizon" --data "$DATA" --device cuda --stage predict
done
echo "$(date) all Gate 2 models done"
