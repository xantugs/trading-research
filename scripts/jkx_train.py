"""Train the JKX CNN ensemble for one model Ix/Ry and predict the test years.

    python -m scripts.jkx_train --days 20 --horizon 5 --device cuda

Images come from scripts.jkx_build_images at the rebalancing frequency of the horizon (R5
weekly, R20 monthly, R60 quarterly). Training uses 1993-2000 only (random 70/30 split per
seed); the fitted models are then fixed for every test year, as in the paper. Writes
<data>/models/I<d>R<h>_seed<k>.pt and <data>/predictions/I<d>R<h>.parquet.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from orb_research.jkx_cnn import n_params, predict, train_one, ChartCNN
from orb_research.jkx_images import FREQ_OF_HORIZON
from orb_research.vault import record_run


def load(folder: Path, years) -> tuple[pd.DataFrame, np.ndarray]:
    metas, packs = [], []
    for y in years:
        m, i = folder / f"{y}_meta.parquet", folder / f"{y}_images.npy"
        if m.exists():
            metas.append(pd.read_parquet(m))
            packs.append(np.load(i))
    if not metas:
        raise FileNotFoundError(f"no image files in {folder} for {list(years)}")
    return pd.concat(metas, ignore_index=True), np.concatenate(packs)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, choices=(5, 20, 60), required=True)
    p.add_argument("--horizon", type=int, choices=(5, 20, 60), required=True)
    p.add_argument("--data", type=Path, default=Path("C:/data/jkx"))
    p.add_argument("--train-years", default="1993-2000")
    p.add_argument("--test-years", default="2001-2019")
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--max-epochs", type=int, default=100)
    p.add_argument("--limit", type=int, default=0, help="debug: use only this many training images")
    p.add_argument("--data-on-cpu", action="store_true", help="keep images in RAM (for a GPU shared with other jobs)")
    p.add_argument("--stage", choices=("all", "train", "predict"), default="all",
                   help="train: fit --seed-list only (run several at once on one GPU); predict: ensemble saved seeds")
    p.add_argument("--seed-list", default=None, help="e.g. 0,1 (default: all seeds)")
    a = p.parse_args()

    name = f"I{a.days}R{a.horizon}"
    folder = a.data / f"I{a.days}_{FREQ_OF_HORIZON[a.horizon]}"
    t0, t1 = map(int, a.train_years.split("-"))
    s0, s1 = map(int, a.test_years.split("-"))
    models = a.data / "models"
    seeds = [int(x) for x in a.seed_list.split(",")] if a.seed_list else list(range(a.seeds))
    if a.stage in ("all", "train"):
        meta, packed = load(folder, range(t0, t1 + 1))
        label = meta[f"ret_{a.horizon}d"].to_numpy()
        keep = np.isfinite(label)
        meta, packed, y = meta[keep].reset_index(drop=True), packed[keep], (label[keep] > 0).astype(np.int64)
        if a.limit:
            meta, packed, y = meta.iloc[: a.limit], packed[: a.limit], y[: a.limit]
        print(f"{name}: {len(y):,} training images {t0}-{t1}, {y.mean():.3f} up, device {a.device}, "
              f"{n_params(ChartCNN(a.days)):,} parameters, seeds {seeds}", flush=True)
        models.mkdir(parents=True, exist_ok=True)
        for seed in seeds:
            fit = train_one(packed, y, a.days, seed, a.device, max_epochs=a.max_epochs,
                            data_on_gpu=not a.data_on_cpu, log=lambda s: print(s, flush=True))
            torch.save(fit, models / f"{name}_seed{seed}.pt")
        del packed
    if a.stage == "train":
        return
    fits = [torch.load(models / f"{name}_seed{k}.pt", weights_only=False) for k in range(a.seeds)]

    test_meta, test_packed = load(folder, range(s0, s1 + 1))
    probs = np.stack([predict(f, test_packed, a.days, a.device) for f in fits], axis=1)
    out = test_meta[["date", "permno", "mcap", "hold_ret", f"ret_{a.horizon}d"]].copy()
    out["prob"] = probs.mean(axis=1)
    for k in range(probs.shape[1]):
        out[f"prob_seed{k}"] = probs[:, k]
    (a.data / "predictions").mkdir(parents=True, exist_ok=True)
    out.to_parquet(a.data / "predictions" / f"{name}.parquet", index=False)
    acc = ((out["prob"] > 0.5) == (out[f"ret_{a.horizon}d"] > 0))[out[f"ret_{a.horizon}d"].notna()].mean()
    print(f"{name}: test accuracy {acc:.4f} on {len(out):,} images", flush=True)
    record_run(f"jkx/{name}", {"days": a.days, "horizon": a.horizon, "train": a.train_years, "test": a.test_years,
                               "seeds": a.seeds, "limit": a.limit},
               {"test_accuracy": float(acc), "epochs": [f["epochs"] for f in fits],
                "best_valid_loss": [f["best_valid_loss"] for f in fits]})


if __name__ == "__main__":
    main()
