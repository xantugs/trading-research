"""XGBoost and a supervised-autoencoder MLP, blended (docs/ML_PLAN.md, after the 1st-place
Jane Street Market Prediction solution). Labels are binary (multi-label allowed); sample
weights scale each row's loss."""
from __future__ import annotations

import numpy as np
import torch
from torch import nn

XGB_PARAMS = {"tree_method": "hist", "max_depth": 5, "learning_rate": 0.03, "subsample": 0.8,
              "colsample_bytree": 0.7, "min_child_weight": 50, "objective": "binary:logistic",
              "eval_metric": "logloss", "n_jobs": 12}


def _unit_mean(weights) -> np.ndarray:
    weights = np.asarray(weights, dtype=np.float64)
    return weights / weights.mean()


def fit_xgb(x_train, y_train, w_train, x_valid, y_valid, w_valid, seed: int = 0):
    """One booster per label column, early-stopped on validation log-loss.

    `min_child_weight` counts summed (hessian x sample) weight, so the weights are scaled
    to mean 1 first: raw |return| weights (~0.003) made every leaf too light to split and
    the boosters predicted a constant (docs/ML_PLAN.md, follow-up 2). A booster that still
    makes no split is reported, never silently kept."""
    import warnings

    import xgboost as xgb

    w_train, w_valid = _unit_mean(w_train), _unit_mean(w_valid)
    models = []
    for j in range(y_train.shape[1]):
        dtrain = xgb.DMatrix(x_train, label=y_train[:, j], weight=w_train)
        dvalid = xgb.DMatrix(x_valid, label=y_valid[:, j], weight=w_valid)
        booster = xgb.train({**XGB_PARAMS, "seed": seed}, dtrain, num_boost_round=2000,
                            evals=[(dvalid, "valid")], early_stopping_rounds=100, verbose_eval=False)
        if not booster.get_score():
            warnings.warn(f"XGBoost label {j}: no split in any tree; it predicts a constant", RuntimeWarning,
                          stacklevel=2)
        models.append(booster)
    return models


def predict_xgb(models, x) -> np.ndarray:
    import xgboost as xgb

    data = xgb.DMatrix(x)
    return np.stack([m.predict(data, iteration_range=(0, m.best_iteration + 1)) for m in models], axis=1)


class Standardiser:
    def fit(self, x):
        self.mean = np.nanmean(x, axis=0)
        self.std = np.nanstd(x, axis=0) + 1e-9
        return self

    def transform(self, x):
        z = (x - self.mean) / self.std
        return np.nan_to_num(np.clip(z, -10, 10), nan=0.0).astype(np.float32)


class AEMLP(nn.Module):
    """Supervised autoencoder + MLP: noisy encoder -> 32-d code, decoder back to the inputs,
    an auxiliary head on the code, and the main MLP on [inputs, code]."""

    def __init__(self, n_in: int, n_out: int):
        super().__init__()
        self.noise = 0.05
        self.encoder = nn.Sequential(nn.BatchNorm1d(n_in), nn.Linear(n_in, 96), nn.SiLU(), nn.Linear(96, 32), nn.SiLU())
        self.decoder = nn.Sequential(nn.Linear(32, 96), nn.SiLU(), nn.Linear(96, n_in))
        self.aux = nn.Linear(32, n_out)
        self.head = nn.Sequential(nn.BatchNorm1d(n_in + 32), nn.Dropout(0.2), nn.Linear(n_in + 32, 256),
                                  nn.BatchNorm1d(256), nn.SiLU(), nn.Dropout(0.2), nn.Linear(256, 128),
                                  nn.BatchNorm1d(128), nn.SiLU(), nn.Dropout(0.2), nn.Linear(128, n_out))

    def forward(self, x):
        noisy = x + self.noise * torch.randn_like(x) if self.training else x
        code = self.encoder(noisy)
        return self.decoder(code), self.aux(code), self.head(torch.cat([x, code], dim=1))


def _build_aemlp(n_in: int, n_out: int):
    return AEMLP(n_in, n_out)


def fit_aemlp(x_train, y_train, w_train, x_valid, y_valid, w_valid, seed: int = 0,
              max_epochs: int = 200, patience: int = 10):
    import torch
    from torch import nn

    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(12)
    model = _build_aemlp(x_train.shape[1], y_train.shape[1])
    optimiser = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    bce = nn.BCEWithLogitsLoss(reduction="none")
    xt, yt = torch.tensor(x_train), torch.tensor(y_train, dtype=torch.float32)
    wt = torch.tensor(w_train / w_train.mean(), dtype=torch.float32)
    xv, yv = torch.tensor(x_valid), torch.tensor(y_valid, dtype=torch.float32)
    wv = torch.tensor(w_valid / w_valid.mean(), dtype=torch.float32)
    best, best_state, waited = np.inf, None, 0
    for _ in range(max_epochs):
        model.train()
        order = torch.randperm(len(xt))
        for start in range(0, len(xt), 512):
            idx = order[start:start + 512]
            if len(idx) < 2:
                continue
            recon, aux, main = model(xt[idx])
            w = wt[idx][:, None]
            loss = (((recon - xt[idx]) ** 2).mean() + (bce(aux, yt[idx]) * w).mean()
                    + (bce(main, yt[idx]) * w).mean())
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
        model.eval()
        with torch.no_grad():
            _, _, main = model(xv)
            valid_loss = float((bce(main, yv) * wv[:, None]).mean())
        if valid_loss < best - 1e-5:
            best, waited = valid_loss, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            waited += 1
            if waited >= patience:
                break
    model.load_state_dict(best_state)
    model.eval()
    return model


def predict_aemlp(model, x) -> np.ndarray:
    import torch

    with torch.no_grad():
        _, _, main = model(torch.tensor(x))
        return torch.sigmoid(main).numpy()


class Blend:
    """XGBoost (raw features) + 3-seed supervised-autoencoder MLP (standardised features)."""

    def fit(self, x_train, y_train, w_train, x_valid, y_valid, w_valid, seeds=(0, 1, 2)):
        self.scaler = Standardiser().fit(x_train)
        self.xgb = fit_xgb(x_train, y_train, w_train, x_valid, y_valid, w_valid)
        zt, zv = self.scaler.transform(x_train), self.scaler.transform(x_valid)
        self.nets = [fit_aemlp(zt, y_train, w_train, zv, y_valid, w_valid, seed=s) for s in seeds]
        return self

    def predict(self, x) -> np.ndarray:
        """Mean probability over labels, blended equally between XGBoost and the MLPs."""
        p_xgb = predict_xgb(self.xgb, x).mean(axis=1)
        z = self.scaler.transform(x)
        p_mlp = np.mean([predict_aemlp(net, z).mean(axis=1) for net in self.nets], axis=0)
        return 0.5 * p_xgb + 0.5 * p_mlp
