from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .models import Mode, TradeCandidate


@dataclass(frozen=True)
class ExecutionReceipt:
    request_id: str
    setup_id: str
    accepted: bool
    mode: Mode
    message: str
    recorded_at: datetime


class Executor(Protocol):
    def submit(self, request_id: str, candidate: TradeCandidate) -> ExecutionReceipt: ...


class ReplayExecutor:
    """Records intent without contacting MT5."""

    def __init__(self) -> None:
        self._receipts: dict[str, ExecutionReceipt] = {}

    def submit(self, request_id: str, candidate: TradeCandidate) -> ExecutionReceipt:
        if request_id not in self._receipts:
            self._receipts[request_id] = ExecutionReceipt(
                request_id=request_id,
                setup_id=candidate.setup_id,
                accepted=True,
                mode=Mode.REPLAY,
                message="recorded_without_order",
                recorded_at=datetime.now(timezone.utc),
            )
        return self._receipts[request_id]


class RealExecutionLocked(RuntimeError):
    pass


class LockedRealExecutor:
    def submit(self, request_id: str, candidate: TradeCandidate) -> ExecutionReceipt:
        raise RealExecutionLocked("REAL execution is disabled in version 0.1.0")
