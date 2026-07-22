"""Incident lifecycle domain."""

from oncall.incidents.state import (
    IncidentStatus,
    InvalidIncidentTransition,
    ensure_transition,
)

__all__ = ["IncidentStatus", "InvalidIncidentTransition", "ensure_transition"]
