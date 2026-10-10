"""Train the idea B CNN for one decision time (docs/INTRADAY_CHARTS_PLAN.md) and predict.

    python -m scripts.intraday_b_train --key 1200 --stage train --seed-list 0   # one seed (run 5 at once)
    python -m scripts.intraday_b_train --key 1200 --stage predict             # ensemble -> predictions

The JKX 20-day CNN on 64 x 3(78 + n_T) images. Trains on 2012-2016, early-stops on 2017 (fixed
validation year). Predictions: <data>/predictions/T<key>_dev.parquet (2012-2017) and
T<key>_test.parquet (2018-2020; evaluated once, by `intraday_b_eval test`).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from orb_research.intraday_b import DECISIONS, HEIGHT, N_BARS
from orb_research.jkx_cnn import ChartCNN, n_params, predict, train_one
from orb_research.vault import record_run


def load(folder: Path, years) -> tuple[pd.DataFrame, np.ndarray]:
    metas, packs = [], []
    for y in years:
        metas.append(pd.read_parquet(folder / f"{y}_meta.parquet"))
        packs.append(np.load(folder / f"{y}_images.npy"))
    return pd.concat(metas, ignore_index=True), np.concatenate(packs)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--key", choices=tuple(DECISIONS), required=True)
    p.add_argument("--data", type=Path, default=Path("C:/data/intraday_b"))
    p.add_argument("--stage", choices=("train", "predict"), required=True)
    p.add_argument("--seed-list", default="0,1,2,3,4")
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--max-epochs", type=int, default=100)
    p.add_argument("--data-on-cpu", action="store_true")
    p.add_argument("--predict-batch", type=int, default=4096, help="images per forward pass at predict time (64 x 396 needs <= 1024 on 24 GB)")
    a = p.parse_args()

    n_t = DECISIONS[a.key]
    shape = (HEIGHT, 3 * (N_BARS + n_t))
    name = f"T{a.key}"
    folder = a.data / name
    models = a.data / "models"
    models.mkdir(parents=True, exist_ok=True)
    if a.stage == "train":
        meta, packed = load(folder, range(2012, 2018))
        keep = (meta["label"] >= 0).to_numpy()
        meta, packed = meta[keep].reset_index(drop=True), packed[keep]
        y = meta["label"].to_numpy().astype(np.int64)
        valid_idx = np.flatnonzero(meta["date"].dt.year == 2017)
        print(f"{name}: {len(y):,} images (train 2012-2016: {len(y) - len(valid_idx):,}; valid 2017: "
              f"{len(valid_idx):,}), shape {shape}, {n_params(ChartCNN(20, shape)):,} parameters", flush=True)
        for seed in [int(s) for s in a.seed_list.split(",")]:
            fit = train_one(packed, y, 20, seed, a.device, max_epochs=a.max_epochs, data_on_gpu=not a.data_on_cpu,
                            shape=shape, valid_idx=valid_idx, log=lambda s: print(s, flush=True))
            torch.save(fit, models / f"{name}_seed{seed}.pt")
        return

    fits = [torch.load(models / f"{name}_seed{k}.pt", weights_only=False) for k in range(a.seeds)]
    out_dir = a.data / "predictions"
    out_dir.mkdir(parents=True, exist_ok=True)
    for part, years in (("dev", range(2012, 2018)), ("test", range(2018, 2021))):
        meta, packed = load(folder, years)
        probs = np.stack([predict(f, packed, 20, a.device, batch=a.predict_batch) for f in fits], axis=1)
        meta = meta.assign(prob=probs.mean(axis=1))
        for k in range(probs.shape[1]):
            meta[f"prob_seed{k}"] = probs[:, k]
        meta.to_parquet(out_dir / f"{name}_{part}.parquet", index=False)
        print(f"{name} {part}: {len(meta):,} predictions", flush=True)
    record_run(f"intraday_b/{name}", {"key": a.key, "seeds": a.seeds, "shape": list(shape)},
               {"epochs": [f["epochs"] for f in fits], "best_valid_loss": [f["best_valid_loss"] for f in fits]})


if __name__ == "__main__":
    main()
