"""Tick-by-tick simulator for the Trading Steady ORB scalping EA.

The EA (MT5, shown on OANDA US100) waits for the 15-minute candle that ends at the New
York open, places a buy stop at its high and a sell stop at its low, and manages every
fill with a fixed stop and target plus a very tight trailing stop that moves on each
tick once the trade is a few points in profit.  Pending orders are removed at a cutoff.

This replays that on native FTMO bid/ask ticks, in the broker's clock (New York + 7 h,
verified by scripts/verify_broker_clock.py), the way the MT5 tester does it:

* The range candle is the bid high and low of [range_open, range_open + range_minutes).
* A buy stop triggers when the ask reaches it, a sell stop when the bid does.  An order
  already inside the spread when placed is rejected, as MT5 rejects a buy stop below
  the ask (configurable).
* Every market fill - entries and stop exits - happens at the first tick at least
  execution_delay_ms after the trigger, at that tick's ask (buys) or bid (sells); the
  EA video used 130 ms.  Targets are limits and fill at their price.
* The stop in force on a tick is the one set by earlier ticks: a tick can trail the
  stop or hit it, not both.  Long positions exit on the bid, shorts on the ask.
* Size = risk_percent of equity (balance plus open profit) over the stop distance,
  floored to the lot step, then reduced if margin at `leverage` does not cover it.

Inputs are in symbol points, so the EA's 200/10-point settings mean $2.00 / $0.10 on
FTMO XAUUSD (point 0.01).  Only ticks before development_end are ever read; past the
2025 holdout boundary only when holdout_opened is set.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

BROKER_OFFSET = timedelta(hours=7)  # FTMO-Demo server clock = New York + 7 h, year-round
MS = 1000


@dataclass(frozen=True)
class ScalperConfig:
    symbol: str = "XAUUSD"
    expected_server: str = "FTMO-Demo"
    tick_cache_dir: str = "C:/data/ftmo_xauusd_ticks"
    output_dir: str = "outputs/runs"
    start: str = "2021-10-04"  # first FTMO real-tick day
    development_end: str = "2025-01-01"  # exclusive; 2025 onward stays sealed
    holdout_start: str = "2025-01-01"
    holdout_opened: str | None = None  # see orb_research.config.Config.holdout_opened
    session_timezone: str = "America/New_York"
    broker_clock: str = "new_york_plus_7"
    range_open: str = "09:15"  # New York; 16:15 in the broker clock, as in the video
    range_minutes: int = 15
    cutoff: str = "12:00"  # New York; pending orders are deleted here (19:00 broker)
    trade_direction: str = "both"
    one_cancels_other: bool = False
    close_at_cutoff: bool = False
    reject_inside_spread: bool = True
    point: float = 0.01
    sl_points: float = 200
    tp_points: float = 200
    trail_points: float = 10
    trail_start_points: float = 10  # profit before trailing starts; not stated in the video
    execution_delay_ms: int = 130
    risk_percent: float = 2.0
    leverage: float = 30.0
    initial_equity: float = 10000.0
    contract_size: float = 100.0
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    commission_per_lot_side: float = 0.0
    commission_rate_pct_side: float = 0.0005

    def validate(self) -> None:
        start, end = date.fromisoformat(self.start), date.fromisoformat(self.development_end)
        if self.holdout_opened is not None:
            date.fromisoformat(self.holdout_opened)
            if not start < end:
                raise ValueError("start < development_end is required")
        elif not start < end <= date.fromisoformat(self.holdout_start):
            raise ValueError("start < development_end <= holdout_start is required "
                             "(set holdout_opened to read past the holdout boundary)")
        if self.broker_clock != "new_york_plus_7":
            raise ValueError("the tick cache is decoded with the verified New York + 7 h clock only")
        if self.session_timezone != "America/New_York":
            raise ValueError("session times are New York times")
        for label in (self.range_open, self.cutoff):
            time.fromisoformat(label)
        if not self.range_minutes > 0 or _minutes(self.range_open) + self.range_minutes > _minutes(self.cutoff):
            raise ValueError("the range must end at or before the cutoff")
        if _minutes(self.cutoff) + 7 * 60 >= 24 * 60:
            raise ValueError("the cutoff must fall on the same broker day")
        if self.trade_direction not in ("both", "long", "short"):
            raise ValueError("trade_direction must be both, long or short")
        for name in ("point", "sl_points", "tp_points", "trail_points", "risk_percent", "leverage",
                     "initial_equity", "contract_size", "lot_step", "min_lot", "max_lot"):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")
        for name in ("trail_start_points", "execution_delay_ms", "commission_per_lot_side",
                     "commission_rate_pct_side"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.risk_percent > 2.0:
            raise ValueError("research risk_percent is capped at 2%")


def _minutes(label: str) -> int:
    parsed = time.fromisoformat(label)
    return parsed.hour * 60 + parsed.minute


def load_scalper_config(path: str | Path) -> ScalperConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw.pop("strategy", None)
    unknown = sorted(set(raw) - {field.name for field in fields(ScalperConfig)})
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(unknown)}")
    config = ScalperConfig(**raw)
    config.validate()
    return config


def cached_tick_loader(config: ScalperConfig) -> Callable[[date], pd.DataFrame | None]:
    """Read one broker-day tick file; refuses any day at or after development_end."""
    root = Path(config.tick_cache_dir) / config.expected_server / config.symbol
    sealed = date.fromisoformat(config.development_end)

    def load(day: date) -> pd.DataFrame | None:
        if day >= sealed:
            raise ValueError(f"refusing to read {day}: on or after development_end {config.development_end}")
        path = root / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}.parquet"
        if not path.is_file():
            return None
        frame = pd.read_parquet(path, columns=["time_msc", "bid", "ask"])
        frame = frame[(frame.bid > 0) & (frame.ask >= frame.bid)]
        return frame.sort_values("time_msc", kind="stable").reset_index(drop=True)

    return load


def _broker_ms(day: date, new_york: str) -> int:
    """Stored-clock epoch milliseconds of a New York wall time on that broker day."""
    stamp = datetime.combine(day, time.fromisoformat(new_york)) + BROKER_OFFSET
    return int(pd.Timestamp(stamp, tz="UTC").value // 1_000_000)


def to_utc(broker_ms: int) -> pd.Timestamp:
    """Decode a stored broker-clock time to true UTC (New York + 7 h -> New York -> UTC)."""
    naive = pd.Timestamp(broker_ms, unit="ms") - BROKER_OFFSET
    return naive.tz_localize("America/New_York").tz_convert("UTC")


@dataclass
class _Fill:
    direction: int
    trigger_ms: int
    fill_index: int
    entry: float


class TickScalper:
    def __init__(self, config: ScalperConfig, load_day: Callable[[date], pd.DataFrame | None] | None = None):
        config.validate()
        self.config = config
        self.load_day = load_day or cached_tick_loader(config)
        self.counts = {"days": 0, "no_tick_file": 0, "no_range_ticks": 0, "flat_range": 0,
                       "buy_stop_rejected": 0, "sell_stop_rejected": 0, "no_trigger": 0,
                       "cancelled_by_oco": 0, "below_min_lot": 0, "margin_reduced": 0,
                       "open_at_data_end": 0}

    # -- one broker day ------------------------------------------------------------------
    def _orders(self, day: date, ticks: pd.DataFrame):
        c = self.config
        t = ticks.time_msc.to_numpy()
        bid, ask = ticks.bid.to_numpy(), ticks.ask.to_numpy()
        open_ms = _broker_ms(day, c.range_open)
        end_ms = open_ms + c.range_minutes * 60 * MS
        cutoff_ms = _broker_ms(day, c.cutoff)
        in_range = (t >= open_ms) & (t < end_ms)
        if not in_range.any():
            self.counts["no_range_ticks"] += 1
            return None
        high, low = float(bid[in_range].max()), float(bid[in_range].min())
        if high <= low:
            self.counts["flat_range"] += 1
            return None
        placed = int(np.searchsorted(t, end_ms))
        last = int(np.searchsorted(t, cutoff_ms))  # orders are deleted at the cutoff
        fills = []
        for direction, level, prices, wanted in ((1, high, ask, "long"), (-1, low, bid, "short")):
            if c.trade_direction not in ("both", wanted):
                continue
            if placed >= len(t):
                continue
            if c.reject_inside_spread and direction * (prices[placed] - level) >= 0:
                self.counts["buy_stop_rejected" if direction == 1 else "sell_stop_rejected"] += 1
                continue
            window = prices[placed:last]
            hit = np.flatnonzero(window >= level if direction == 1 else window <= level)
            if not len(hit):
                continue
            trigger = placed + int(hit[0])
            fill = self._delayed(t, int(t[trigger]))
            if fill is None:
                continue
            fills.append(_Fill(direction, int(t[trigger]), fill,
                               float(ask[fill] if direction == 1 else bid[fill])))
        fills.sort(key=lambda f: f.trigger_ms)
        if c.one_cancels_other and len(fills) == 2:
            fills = fills[:1]
            self.counts["cancelled_by_oco"] += 1
        if not fills:
            self.counts["no_trigger"] += 1
        return {"high": high, "low": low, "open_ms": open_ms, "end_ms": end_ms,
                "cutoff_ms": cutoff_ms, "fills": fills}

    def _delayed(self, t: np.ndarray, trigger_ms: int) -> int | None:
        index = int(np.searchsorted(t, trigger_ms + self.config.execution_delay_ms))
        return index if index < len(t) else None

    def _exit(self, ticks: pd.DataFrame, fill: _Fill, cutoff_ms: int):
        """Walk the position forward (across later broker days if needed) to its exit."""
        c = self.config
        risk = c.sl_points * c.point
        d = fill.direction
        stop0 = fill.entry - d * risk
        target = fill.entry + d * c.tp_points * c.point
        trail, start = c.trail_points * c.point, c.trail_start_points * c.point
        frame, offset, extra_days = ticks, fill.fill_index + 1, 0
        stop_now, mae, mfe = stop0, 0.0, 0.0
        while True:
            t = frame.time_msc.to_numpy()[offset:]
            price = (frame.bid if d == 1 else frame.ask).to_numpy()[offset:]
            if len(t):
                gain = d * (price - fill.entry)
                proposed = np.where(gain >= start, price - d * trail, np.nan)
                if d == 1:
                    trailed = np.fmax.accumulate(np.concatenate([[stop_now], proposed]))
                else:
                    trailed = np.fmin.accumulate(np.concatenate([[stop_now], proposed]))
                in_force = trailed[:-1]  # the stop set before each tick
                stopped = d * (price - in_force) <= 0
                targeted = d * (price - target) >= 0
                timed = (t >= cutoff_ms) if c.close_at_cutoff and extra_days == 0 else np.zeros(len(t), bool)
                events = np.flatnonzero(stopped | targeted | timed)
                upto = events[0] + 1 if len(events) else len(t)
                mae = max(mae, float(np.max(-gain[:upto], initial=0)) / risk)
                mfe = max(mfe, float(np.max(gain[:upto], initial=0)) / risk)
                if len(events):
                    i = int(events[0])
                    full_t = frame.time_msc.to_numpy()
                    if targeted[i] and not stopped[i]:
                        return t[i], target, "target", mae, mfe
                    reason = ("time" if timed[i] and not stopped[i]
                              else "stop" if in_force[i] == stop0 else "trail_stop")
                    # A delayed fill past the file's last tick takes that last tick.
                    fill_at = self._delayed(full_t, int(t[i]))
                    fill_at = len(full_t) - 1 if fill_at is None else fill_at
                    exit_price = float((frame.bid if d == 1 else frame.ask).iloc[fill_at])
                    mae = max(mae, d * (fill.entry - exit_price) / risk)
                    return int(full_t[fill_at]), exit_price, reason, mae, mfe
                stop_now = float(trailed[-1])
            # Still open at the end of this broker day: continue on the next file.
            nxt = None
            day = pd.Timestamp(int(frame.time_msc.iloc[-1]), unit="ms").date()
            while nxt is None:
                day += timedelta(days=1)
                extra_days += 1
                if day >= date.fromisoformat(c.development_end) or extra_days > 10:
                    return None
                nxt = self.load_day(day)
            frame, offset = nxt, 0

    # -- the whole period ----------------------------------------------------------------
    def run(self) -> tuple[pd.DataFrame, dict]:
        c = self.config
        balance = c.initial_equity
        records: list[dict] = []
        day = date.fromisoformat(c.start)
        end = date.fromisoformat(c.development_end)
        while day < end:
            if day.weekday() < 5:
                self._run_day(day, records)
            day += timedelta(days=1)
        # Book P/L in exit order; equity_after is the closed balance after each exit.
        frame = pd.DataFrame.from_records(records)
        if not frame.empty:
            frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
            booked = frame.sort_values("exit_time", kind="stable")
            frame["equity_after"] = balance + booked.PnL.cumsum()
        return frame, {**self.counts, "trades": len(frame)}

    def _balance_at(self, records: list[dict], ms: int) -> float:
        return self.config.initial_equity + sum(r["PnL"] for r in records if r["_exit_ms"] < ms)

    def _run_day(self, day: date, records: list[dict]) -> None:
        c = self.config
        ticks = self.load_day(day)
        self.counts["days"] += 1
        if ticks is None or ticks.empty:
            self.counts["no_tick_file"] += 1
            return
        orders = self._orders(day, ticks)
        if not orders:
            return
        t, bid, ask = ticks.time_msc.to_numpy(), ticks.bid.to_numpy(), ticks.ask.to_numpy()
        for fill in orders["fills"]:
            outcome = self._exit(ticks, fill, orders["cutoff_ms"])
            if outcome is None:
                self.counts["open_at_data_end"] += 1
                continue
            exit_ms, exit_price, reason, mae, mfe = outcome
            fill_ms = int(t[fill.fill_index])
            # Equity at the fill: closed balance plus open profit of positions still running.
            equity = self._balance_at(records, fill_ms)
            used_margin = 0.0
            for r in records:
                if r["_entry_ms"] <= fill_ms < r["_exit_ms"]:
                    mark = bid[fill.fill_index] if r["_dir"] == 1 else ask[fill.fill_index]
                    equity += r["_dir"] * (mark - r["entry"]) * r["position_size"] * c.contract_size
                    used_margin += r["position_size"] * c.contract_size * r["entry"] / c.leverage
            lots, per_lot = self._size(equity, used_margin, fill.entry)
            if lots == 0:
                self.counts["below_min_lot"] += 1
                continue
            commission = lots * (2 * c.commission_per_lot_side + (abs(fill.entry) + abs(exit_price))
                                 * c.contract_size * c.commission_rate_pct_side / 100)
            pnl = fill.direction * (exit_price - fill.entry) * lots * c.contract_size - commission
            estimated_risk = lots * per_lot
            records.append({
                "timestamp": to_utc(fill_ms), "session_date": day.isoformat(),
                "session": "tick_scalper", "direction": "long" if fill.direction == 1 else "short",
                "range_start": to_utc(orders["open_ms"]), "range_end": to_utc(orders["end_ms"]),
                "range_high": orders["high"], "range_low": orders["low"],
                "signal_open": to_utc(fill.trigger_ms), "signal_close": to_utc(fill.trigger_ms),
                "signal_price": orders["high"] if fill.direction == 1 else orders["low"],
                "entry": fill.entry, "stop": fill.entry - fill.direction * c.sl_points * c.point,
                "take_profit": fill.entry + fill.direction * c.tp_points * c.point,
                "exit_time": to_utc(exit_ms), "exit": exit_price, "exit_reason": reason,
                "position_size": lots, "risk_percent": c.risk_percent,
                "estimated_risk": estimated_risk, "R_result": pnl / estimated_risk,
                "PnL": pnl, "commission": commission, "financing": 0.0, "MAE": mae, "MFE": mfe,
                "ambiguous_m1": False, "entry_spread": float(ask[fill.fill_index] - bid[fill.fill_index]),
                "_entry_ms": fill_ms, "_exit_ms": exit_ms, "_dir": fill.direction,
            })

    def _size(self, equity: float, used_margin: float, entry: float) -> tuple[float, float]:
        c = self.config
        distance = c.sl_points * c.point
        per_lot = distance * c.contract_size + (2 * c.commission_per_lot_side + 2 * abs(entry)
                                                * c.contract_size * c.commission_rate_pct_side / 100)
        requested = equity * c.risk_percent / 100 / per_lot
        affordable = max(0.0, equity - used_margin) * c.leverage / (entry * c.contract_size)
        if affordable < requested:
            self.counts["margin_reduced"] += 1
        steps = math.floor(min(requested, affordable, c.max_lot) / c.lot_step + 1e-10)
        lots = round(steps * c.lot_step, 8)
        return (lots, per_lot) if lots >= c.min_lot else (0.0, per_lot)


def ledger(trades: pd.DataFrame) -> pd.DataFrame:
    """The trade ledger without the simulator's private bookkeeping columns."""
    return trades.drop(columns=[name for name in trades.columns if name.startswith("_")])


def serialise(config: ScalperConfig) -> dict:
    return {"strategy": "tick_scalper", **asdict(config)}
