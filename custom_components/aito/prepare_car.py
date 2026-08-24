from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any


COMMAND_STATE_IDLE = "idle"
COMMAND_STATE_SENDING = "sending"
COMMAND_STATE_PENDING_CONFIRMATION = "pending_confirmation"
COMMAND_STATE_CONFIRMED = "confirmed"
COMMAND_STATE_FAILED = "failed"


class PrepareCarCooldownError(RuntimeError):
    def __init__(self, remaining_seconds: int) -> None:
        super().__init__(f"AITO prepare-car command is cooling down for {remaining_seconds} seconds")
        self.remaining_seconds = remaining_seconds


@dataclass
class _CommandRecord:
    state: str = COMMAND_STATE_IDLE
    action: str | None = None
    requested_at: str | None = None
    confirmed_at: str | None = None
    error_code: str | None = None
    readback_attempts: int = 0
    cooldown_until: float = 0.0


class PrepareCarCommandTracker:
    """In-memory, privacy-safe command state exposed through the switch entity."""

    def __init__(self, *, cooldown_seconds: int = 60) -> None:
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self._records: dict[str, _CommandRecord] = {}

    def begin(
        self,
        vehicle_id: str,
        *,
        enabled: bool,
        now_monotonic: float,
        requested_at: datetime,
    ) -> None:
        remaining = self.cooldown_remaining(vehicle_id, now_monotonic=now_monotonic)
        if remaining:
            raise PrepareCarCooldownError(remaining)
        self._records[vehicle_id] = _CommandRecord(
            state=COMMAND_STATE_SENDING,
            action="enable" if enabled else "disable",
            requested_at=requested_at.isoformat(),
            cooldown_until=now_monotonic + self.cooldown_seconds,
        )

    def mark_pending(self, vehicle_id: str) -> None:
        self._record(vehicle_id).state = COMMAND_STATE_PENDING_CONFIRMATION

    def record_readback(self, vehicle_id: str, attempt: int) -> None:
        self._record(vehicle_id).readback_attempts = max(0, int(attempt))

    def mark_confirmed(self, vehicle_id: str, *, confirmed_at: datetime) -> None:
        record = self._record(vehicle_id)
        record.state = COMMAND_STATE_CONFIRMED
        record.confirmed_at = confirmed_at.isoformat()
        record.error_code = None

    def mark_failed(self, vehicle_id: str, *, error_code: str) -> None:
        record = self._record(vehicle_id)
        record.state = COMMAND_STATE_FAILED
        record.error_code = str(error_code or "UNKNOWN")

    def cooldown_remaining(self, vehicle_id: str, *, now_monotonic: float) -> int:
        record = self._records.get(vehicle_id)
        if record is None:
            return 0
        return max(0, math.ceil(record.cooldown_until - now_monotonic))

    def snapshot(self, vehicle_id: str, *, now_monotonic: float) -> dict[str, Any]:
        record = self._records.get(vehicle_id, _CommandRecord())
        return {
            "command_state": record.state,
            "command_action": record.action,
            "command_requested_at": record.requested_at,
            "command_confirmed_at": record.confirmed_at,
            "command_error_code": record.error_code,
            "command_cooldown_remaining_seconds": self.cooldown_remaining(
                vehicle_id,
                now_monotonic=now_monotonic,
            ),
            "command_readback_attempts": record.readback_attempts,
        }

    def _record(self, vehicle_id: str) -> _CommandRecord:
        return self._records.setdefault(vehicle_id, _CommandRecord())


def default_departure_plan(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    departure_plan = data.get("departurePlan")
    if not isinstance(departure_plan, dict):
        return None
    plans = departure_plan.get("departurePlanList")
    if not isinstance(plans, list):
        return None
    return next(
        (
            plan
            for plan in plans
            if isinstance(plan, dict) and plan.get("planId") in {0, "0"}
        ),
        None,
    )


def departure_plan_matches(data: Any, *, enabled: bool) -> bool:
    plan = default_departure_plan(data)
    if plan is None or plan.get("planStatus") is None:
        return False
    is_enabled = plan.get("planStatus") in {0, "0"}
    return is_enabled is enabled
