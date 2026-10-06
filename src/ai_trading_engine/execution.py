from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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


class FileBridgeExecutor:
    """Submits one confirmed real order to the MT5 common-files bridge."""

    def __init__(self, bridge_directory: Path, *, confirmation: str, fixed_volume: float = 0.01) -> None:
        if confirmation != "REAL":
            raise RealExecutionLocked("real order requires confirmation=REAL")
        if fixed_volume != 0.01:
            raise ValueError("real execution volume is fixed at 0.01 lot")
        self.bridge_directory = bridge_directory
        self.fixed_volume = fixed_volume

    def submit(self, request_id: str, candidate: TradeCandidate) -> ExecutionReceipt:
        self.bridge_directory.mkdir(parents=True, exist_ok=True)
        request_path = self.bridge_directory / "request.txt"
        if request_path.exists():
            return ExecutionReceipt(
                request_id=request_id,
                setup_id=candidate.setup_id,
                accepted=False,
                mode=Mode.REAL,
                message="bridge_busy",
                recorded_at=datetime.now(timezone.utc),
            )

        fields = (
            "1",
            request_id,
            candidate.setup_id,
            str(int(candidate.created_at.timestamp())),
            str(int(candidate.expires_at.timestamp())),
            candidate.symbol,
            candidate.direction.value,
            f"{self.fixed_volume:.2f}",
            f"{candidate.stop_loss:.8f}",
            f"{candidate.take_profit:.8f}",
            "30",
        )
        if any("|" in field or "\n" in field or "\r" in field for field in fields):
            raise ValueError("bridge fields contain a forbidden delimiter")
        temporary_path = request_path.with_suffix(".tmp")
        temporary_path.write_text("|".join(fields), encoding="ascii")
        temporary_path.replace(request_path)
        return ExecutionReceipt(
            request_id=request_id,
            setup_id=candidate.setup_id,
            accepted=True,
            mode=Mode.REAL,
            message="submitted_to_mt5",
            recorded_at=datetime.now(timezone.utc),
        )
