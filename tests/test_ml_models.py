"""XGBoost must learn from a planted signal even when sample weights are tiny (|returns|)."""
from __future__ import annotations

import numpy as np
import pytest

xgb = pytest.importorskip("xgboost")

from orb_research.ml_models import fit_xgb, predict_xgb  # noqa: E402


def test_xgboost_splits_with_return_sized_weights() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(4000, 6))
    y = (x[:, :1] + 0.5 * rng.normal(size=(4000, 1)) > 0).astype(np.float32)
    w = np.abs(rng.normal(size=4000)) * 0.003          # the size of the weights that broke it
    boosters = fit_xgb(x[:3000], y[:3000], w[:3000], x[3000:], y[3000:], w[3000:])
    assert boosters[0].get_score(), "XGBoost made no split"
    p = predict_xgb(boosters, x[3000:])[:, 0]
    assert p.std() > 0.05 and ((p > 0.5) == y[3000:, 0]).mean() > 0.7
