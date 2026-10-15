from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .models import Direction, MarketSnapshot, TradeCandidate
from .risk import RiskCheck


def _load(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def has_open_paper_trade(path: Path) -> bool:
    return bool(_load(path).get("open"))


def open_paper_trade(
    path: Path,
    candidate: TradeCandidate,
    risk: RiskCheck,
    *,
    opened_at: datetime,
    breakout_level: float | None = None,
    max_holding_minutes: int = 30,
) -> bool:
    state = _load(path)
    open_trades = list(state.get("open") or [])
    closed_trades = list(state.get("closed") or [])
    known = {
        str(item.get("setup_id"))
        for item in open_trades + closed_trades
        if isinstance(item, dict)
    }
    if open_trades or candidate.setup_id in known:
        return False
    open_trades.append(
        {
            "setup_id": candidate.setup_id,
            "strategy_version": candidate.strategy_version,
            "symbol": candidate.symbol,
            "direction": candidate.direction.value,
            "entry": candidate.entry,
            "stop_loss": candidate.stop_loss,
            "take_profit": candidate.take_profit,
            "volume": candidate.volume,
            "estimated_loss": risk.estimated_loss,
            "net_reward_risk": risk.net_reward_risk,
            "breakout_level": breakout_level,
            "max_holding_minutes": max_holding_minutes,
            "opened_at": opened_at.isoformat(),
        }
    )
    state["open"] = open_trades
    state["closed"] = closed_trades[-500:]
    state["updated_at"] = opened_at.isoformat()
    _write(path, state)
    return True


def settle_paper_trades(path: Path, snapshot: MarketSnapshot) -> list[dict[str, object]]:
    state = _load(path)
    open_trades = list(state.get("open") or [])
    closed_trades = list(state.get("closed") or [])
    still_open: list[dict[str, object]] = []
    settled: list[dict[str, object]] = []

    for item in open_trades:
        if not isinstance(item, dict) or item.get("symbol") != snapshot.symbol:
            if isinstance(item, dict):
                still_open.append(item)
            continue
        direction = Direction(str(item["direction"]))
        entry = float(item["entry"])
        stop = float(item["stop_loss"])
        target = float(item["take_profit"])
        exit_price: float | None = None
        exit_reason: str | None = None
        breakout_level = item.get("breakout_level")
        opened_at = datetime.fromisoformat(str(item["opened_at"]).replace("Z", "+00:00"))
        closed_m5 = [candle for candle in snapshot.closed_candles("M5") if candle.close_time > opened_at]
        latest_closed = closed_m5[-1] if closed_m5 else None
        max_holding = timedelta(minutes=int(item.get("max_holding_minutes", 30)))
        if direction is Direction.BUY:
            if snapshot.quote.bid <= stop:
                exit_price, exit_reason = stop, "SL"
            elif snapshot.quote.bid >= target:
                exit_price, exit_reason = target, "TP"
            elif breakout_level is not None and latest_closed is not None and latest_closed.close < float(breakout_level):
                exit_price, exit_reason = snapshot.quote.bid, "BREAKOUT_INVALIDATED"
            elif snapshot.captured_at - opened_at >= max_holding:
                exit_price, exit_reason = snapshot.quote.bid, "TIME_STOP"
            signed_move = None if exit_price is None else exit_price - entry
        else:
            if snapshot.quote.ask >= stop:
                exit_price, exit_reason = stop, "SL"
            elif snapshot.quote.ask <= target:
                exit_price, exit_reason = target, "TP"
            elif breakout_level is not None and latest_closed is not None and latest_closed.close > float(breakout_level):
                exit_price, exit_reason = snapshot.quote.ask, "BREAKOUT_INVALIDATED"
            elif snapshot.captured_at - opened_at >= max_holding:
                exit_price, exit_reason = snapshot.quote.ask, "TIME_STOP"
            signed_move = None if exit_price is None else entry - exit_price
        if exit_price is None or signed_move is None:
            still_open.append(item)
            continue
        risk_distance = abs(entry - stop)
        r_multiple = signed_move / risk_distance if risk_distance > 0 else 0.0
        estimated_loss = float(item.get("estimated_loss", 0.0))
        outcome = {
            **item,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "closed_at": snapshot.captured_at.isoformat(),
            "pnl": r_multiple * estimated_loss,
            "r_multiple": r_multiple,
            "won": r_multiple > 0,
        }
        settled.append(outcome)
        closed_trades.append(outcome)

    if settled:
        state["open"] = still_open
        state["closed"] = closed_trades[-500:]
        state["updated_at"] = snapshot.captured_at.isoformat()
        _write(path, state)
    return settled
