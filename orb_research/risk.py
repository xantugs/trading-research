"""Equity-risk lot sizing using the actual contract and lot step."""
from __future__ import annotations

import math

from .config import Config


def trade_commission(lots: float, entry: float, exit_price: float, config: Config) -> float:
    """Both sides: fixed per-lot fee plus percent of USD notional per side."""
    notional_rate = config.commission_rate_pct_side / 100.0
    return lots * (2 * config.commission_per_lot_side
                   + (abs(entry) + abs(exit_price)) * config.contract_size * notional_rate)


def size_position(equity: float, entry: float, stop: float, config: Config) -> tuple[float, float]:
    """Return (lots, estimated loss at stop including both commissions/slippage)."""
    distance = abs(entry - stop)
    if not math.isfinite(distance) or distance <= 0:
        return 0.0, 0.0
    slipped_stop = stop + math.copysign(config.slippage_price, stop - entry)
    per_lot = ((distance + config.slippage_price) * config.contract_size
               + trade_commission(1.0, entry, slipped_stop, config))
    requested = equity * config.risk_percent / 100 / per_lot
    steps = math.floor(min(requested, config.max_lot) / config.lot_step + 1e-10)
    lots = round(steps * config.lot_step, 8)
    if lots < config.min_lot:
        return 0.0, 0.0
    return lots, lots * per_lot
