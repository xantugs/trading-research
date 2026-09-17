"""Equity-risk lot sizing using the actual contract and lot step."""
from __future__ import annotations

import math

from .config import Config


def size_position(equity: float, entry: float, stop: float, config: Config) -> tuple[float, float]:
    """Return (lots, estimated loss at stop including both commissions/slippage)."""
    distance = abs(entry - stop)
    if not math.isfinite(distance) or distance <= 0:
        return 0.0, 0.0
    per_lot = ((distance + config.slippage_price) * config.contract_size
               + 2 * config.commission_per_lot_side)
    requested = equity * config.risk_percent / 100 / per_lot
    steps = math.floor(min(requested, config.max_lot) / config.lot_step + 1e-10)
    lots = round(steps * config.lot_step, 8)
    if lots < config.min_lot:
        return 0.0, 0.0
    return lots, lots * per_lot
