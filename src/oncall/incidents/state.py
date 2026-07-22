"""Deterministic Incident lifecycle transitions."""

from enum import StrEnum


class IncidentStatus(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGING = "TRIAGING"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSED = "DIAGNOSED"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    NEED_HUMAN = "NEED_HUMAN"
    FAILED = "FAILED"


class InvalidIncidentTransition(ValueError):
    """Raised when a caller attempts an illegal lifecycle transition."""

    def __init__(self, current: IncidentStatus, target: IncidentStatus) -> None:
        super().__init__(f"illegal incident transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


ALLOWED_TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.RECEIVED: frozenset({IncidentStatus.TRIAGING, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.TRIAGING: frozenset({IncidentStatus.INVESTIGATING, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.DIAGNOSED, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.DIAGNOSED: frozenset({IncidentStatus.PLANNING, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.PLANNING: frozenset({IncidentStatus.WAITING_APPROVAL, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.WAITING_APPROVAL: frozenset({IncidentStatus.EXECUTING, IncidentStatus.PLANNING, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.EXECUTING: frozenset({IncidentStatus.VERIFYING, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.VERIFYING: frozenset({IncidentStatus.RESOLVED, IncidentStatus.PLANNING, IncidentStatus.NEED_HUMAN, IncidentStatus.FAILED}),
    IncidentStatus.NEED_HUMAN: frozenset({IncidentStatus.TRIAGING, IncidentStatus.INVESTIGATING, IncidentStatus.PLANNING, IncidentStatus.EXECUTING, IncidentStatus.VERIFYING, IncidentStatus.FAILED}),
    IncidentStatus.RESOLVED: frozenset(),
    IncidentStatus.FAILED: frozenset(),
}


def _coerce_status(value: IncidentStatus | str) -> IncidentStatus:
    if isinstance(value, IncidentStatus):
        return value
    try:
        return IncidentStatus(value)
    except ValueError as exc:
        raise ValueError(f"unknown incident status: {value!r}") from exc


def ensure_transition(
    current: IncidentStatus | str, target: IncidentStatus | str
) -> IncidentStatus:
    """Validate and return a legal target status."""

    current_status = _coerce_status(current)
    target_status = _coerce_status(target)
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidIncidentTransition(current_status, target_status)
    return target_status
