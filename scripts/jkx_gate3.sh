#!/bin/bash
# Gate 3 of docs/JKX_REPLICATION_PLAN.md, run ONCE on the GPU server (from /root/autodl-tmp/trading):
#   setsid nohup bash scripts/jkx_gate3.sh 2020-2025 > logs/gate3.log 2>&1 &
# The frozen 1993-2000 models (no retraining) predict the post-publication years into
# predictions/I<d>R<h>_post.parquet, then the decile portfolios are evaluated on those years only.
# Pass (pre-registered): I20/R5 equal-weight H-L t (Newey-West) >= 2.0 AND the 500-largest
# equal-weight H-L Sharpe > 0.
set -euo pipefail
cd "$(dirname "$0")/.."
YEARS=${1:?test years, e.g. 2020-2025}
PY=venv/bin/python
for model in "5 5" "20 5" "20 20"; do
  set -- $model
  $PY -m scripts.jkx_train --days "$1" --horizon "$2" --data data --device cuda --stage predict \
    --test-years "$YEARS" --pred-suffix _post
done
CUDA_VISIBLE_DEVICES= $PY -m scripts.jkx_evaluate --data data --test-years "$YEARS" --suffix _post --out outputs/jkx/gate3
$PY - "$YEARS" <<'EOF'
import sys, pandas as pd
r = pd.read_csv("outputs/jkx/gate3/report.csv")
r = r[~r["model"].str.startswith(("MOM", "STR", "WSTR"))]
row = lambda m, w: r[(r.model == m) & (r.weights == w)].iloc[0]
t = row("I20/R5", "ew")["t_nw"]; s500 = row("I20/R5", "ew500")["sharpe"]
ok = t >= 2.0 and s500 > 0
print(f"\nGATE 3 ({sys.argv[1]}): I20/R5 EW H-L t_nw = {t:.2f} (need >= 2.0); "
      f"500-largest EW H-L Sharpe = {s500:.2f} (need > 0) -> {'PASS' if ok else 'FAIL'}")
EOF
