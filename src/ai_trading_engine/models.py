from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Mapping, Sequence


class Mode(StrEnum):
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    DEMO = "DEMO"
    REAL = "REAL"


class Direction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Quote:
    captured_at: datetime
    bid: float
    ask: float


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int = 0
    freeze_level_points: int = 0


@dataclass(frozen=True)
class AccountState:
    equity: float
    day_start_equity: float
    realized_pnl_today: float


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    captured_at: datetime
    symbol: str
    quote: Quote
    symbol_spec: SymbolSpec
    account: AccountState
    candles: Mapping[str, Sequence[Candle]] = field(default_factory=dict)
    open_positions: int = 0
    pending_orders: int = 0

    def closed_candles(self, timeframe: str) -> tuple[Candle, ...]:
        return tuple(c for c in self.candles.get(timeframe, ()) if c.close_time <= self.captured_at)


@dataclass(frozen=True)
class TradeCandidate:
    setup_id: str
    strategy_version: str
    snapshot_id: str
    created_at: datetime
    expires_at: datetime
    symbol: str
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    volume: float


@dataclass(frozen=True)
class ValidationResult:
    decision: Decision
    setup_id: str
    reason: str
    probability: float
    decided_at: datetime
