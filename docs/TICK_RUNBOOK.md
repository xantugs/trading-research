# QQQ tick pull runbook (2018-2022)

One page for the whole operation: array job on WRDS Cloud, per-year archives,
parallel HTTPS transfer to Windows, extraction and verification.

Substitute your own job ID where `40116873` appears.

## 1. Confirm the array finished

```sh
ssh -o User=hantu wrds-cloud.wharton.upenn.edu
cd ~/orb_taq
qstat -t -u hantu
bash wrds_watch_ticks.sh
```

Nothing listed but `spawner-ju` means the array is done. The watcher exits on
its own once all 60 manifests exist.

## 2. Verify on the cluster

```sh
ls /scratch/cuhkcn/hantu/orb_ticks/*/*/manifest.json | wc -l
grep -l "Traceback\|Error" qqq_ticks.o40116873.* 2>/dev/null
du -sh /scratch/cuhkcn/hantu/orb_ticks
python3 -c "
import json,glob
f=m=0
for p in glob.glob('/scratch/cuhkcn/hantu/orb_ticks/*/*/manifest.json'):
    d=json.load(open(p)); f+=len(d['files']); m+=len(d['missing_tables'])
print(f,'files,',m,'missing entries')
"
```

Expected: **60** manifests, no output from `grep`, about **24G**, and exactly
**2518 files, 92 missing entries**.

Neither number is arbitrary. The extractor iterates weekdays only, so weekends
never appear; the 92 gaps are the 46 market holidays in 2018-2022 (nine or ten a
year, including 5 December 2018 for the Bush funeral) x two kinds. And 2518 / 2
= **1,259 trading days**, exactly what those five years contain. If both match,
nothing was silently dropped.

List the gaps to confirm they are holidays rather than real misses:

```sh
python3 -c "
import json,glob
d=set()
for p in glob.glob('/scratch/cuhkcn/hantu/orb_ticks/*/*/manifest.json'):
    for e in json.load(open(p))['missing_tables']: d.add(e['date'])
print(len(d),'dates'); print(sorted(d))
"
```

If a task failed, resubmit only that month: task N maps to
`2018 + (N-1)/12` and month `(N-1)%12 + 1`.

```sh
qsub -t N -v ORB_TICK_ROOT=/scratch/cuhkcn/hantu/orb_ticks wrds_submit_ticks.sh
```

## 3. Pack five yearly archives on scratch

The 2,518 small Parquet files transfer too slowly over sequential SFTP. Parquet
is already compressed, so package each year as an uncompressed tar and expose a
home-directory symlink to JupyterLab:

```sh
mkdir -p /scratch/cuhkcn/hantu/orb_tars ~/orb_tars
for year in 2018 2019 2020 2021 2022; do
  tar -C /scratch/cuhkcn/hantu/orb_ticks -cf \
    /scratch/cuhkcn/hantu/orb_tars/orb_ticks_${year}.tar "${year}"
  ln -sfn /scratch/cuhkcn/hantu/orb_tars/orb_ticks_${year}.tar \
    ~/orb_tars/orb_ticks_${year}.tar
done
ls -lh /scratch/cuhkcn/hantu/orb_tars
```

Observed archive sizes were approximately 2.7, 2.6, 4.8, 6.0 and 7.8 GB.
Creating one 24 GB tar would exceed the local free-space safety margin during
download and extraction.

## 4. Download and extract on Windows

**Not** inside `OneDrive`. A synced folder will try to upload all 26 GB back out
over the same connection you are downloading on.

```bat
mkdir C:\data\wrds_ticks
```

The destination is deliberately outside OneDrive. Confirm at least 50 GB free.
In JupyterLab, open `orb_tars` and download all five tar files in parallel. The
observed aggregate HTTPS rate was roughly 4–5 MB/s, versus about 15 KB/s for
sequential SFTP.

Extract and delete each tar as soon as it finishes so no year needs both its tar
and extracted tree on disk longer than necessary:

```powershell
tar -xf C:\Users\khant\Downloads\orb_ticks_2018.tar -C C:\data\wrds_ticks
Remove-Item -LiteralPath C:\Users\khant\Downloads\orb_ticks_2018.tar
```

Repeat for 2019–2022. Keep the five browser downloads running in parallel, but
extract only completed tars. A browser `.crdownload` or `.part` file is not a
completed archive.

## 5. Verify the download

```bat
cd C:\Users\khant\OneDrive\Documents\ChatGPT\2027\orb-backtest
.venv\Scripts\python scripts\verify_tick_download.py C:\data\wrds_ticks --expected 2518 --expected-manifests 60
```

Want `2518 present, 2518 declared` and `unreadable 0`. Parquet has a magic
footer, so a file cut short by a dropped transfer fails to open - which is
exactly the failure this catches and a size comparison does not.

If anything is broken, remove the unreadable files, re-download the affected
year tar and extract it again:

```bat
.venv\Scripts\python scripts\verify_tick_download.py C:\data\wrds_ticks --delete-bad
```

Then repeat step 4 for that year. Loop until the verifier is clean.

## 6. Remove the temporary cloud tars

Only after local verification reports 60 manifests, 2,518 Parquet files and no
unreadable footer, remove the duplicated tar staging area. The source Parquet
tree remains under `orb_ticks` until the local research copy has been used:

```sh
rm -rf /scratch/cuhkcn/hantu/orb_tars
rm -f ~/orb_tars/orb_ticks_*.tar
```

## Notes

- Scratch is purged on a schedule; check `ls -la /scratch` and any README for
  the window. Do not treat it as storage.
- The completed 2018–2022 extraction contains 1,259 trading days. The 92 missing
  table entries are 46 exchange holidays times two file kinds.
- Keep the data outside the repository. `data/*` is git-ignored, but
  `C:\data\wrds_ticks` sits outside it entirely, which is what you want.
