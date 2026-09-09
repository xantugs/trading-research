#!/bin/bash
# Live progress bar for the qqq_ticks array. Run on the WRDS login node:
#   bash wrds_watch_ticks.sh /scratch/cuhkcn/hantu/orb_ticks
# Ctrl-C to stop watching; it never touches the running job.

root="${1:-/scratch/cuhkcn/hantu/orb_ticks}"
files_total=2518          # 1259 trading days x trades + quotes
months_total=60
target_bytes=25769803776  # ~24 GB estimated from the probe
width=32
interval="${WATCH_INTERVAL:-20}"

t0=$(date +%s)
b0=""

while true; do
  n=$(find "$root" -name '*.parquet' 2>/dev/null | wc -l)
  m=$(find "$root" -name 'manifest.json' 2>/dev/null | wc -l)
  b=$(du -sb "$root" 2>/dev/null | cut -f1)
  b=${b:-0}
  [ -z "$b0" ] && b0=$b
  now=$(date +%s)

  printf '\r%s' "$(awk -v n="$n" -v ft="$files_total" -v m="$m" -v mt="$months_total" \
                       -v b="$b" -v b0="$b0" -v tgt="$target_bytes" \
                       -v el="$((now - t0))" -v w="$width" '
    BEGIN {
      pct = (n >= ft) ? 100 : n * 100 / ft
      fill = int(pct * w / 100)
      bar = ""
      for (i = 0; i < w; i++) bar = bar (i < fill ? "#" : "-")
      eta = "  --"
      if (el > 30 && b > b0) {
        rate = (b - b0) / el
        left = (tgt - b) / rate
        if (left > 0) eta = (left > 5400) ? sprintf("%4.1fh", left/3600) : sprintf("%4.0fm", left/60)
      }
      printf "[%s] %3d%%  %4d/%d files  %5.2f GB  %2d/%d months  ETA %s",
             bar, pct, n, ft, b/1073741824, m, mt, eta
    }')"

  if [ "$m" -ge "$months_total" ]; then
    printf '\ndone: %s months, %s files\n' "$m" "$n"
    break
  fi
  sleep "$interval"
done
