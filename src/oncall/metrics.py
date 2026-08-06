"""Application metrics for the Incident lifecycle and bounded recovery flow."""

from prometheus_client import Counter, Histogram

INCIDENT_GRAPH_RUNS = Counter("oncall_incident_graph_runs_total", "Incident graph runs by terminal state.", ("status",))
RECOVERY_EXECUTIONS = Counter("oncall_recovery_executions_total", "Approved recovery executions by result.", ("status",))
RECOVERY_VERIFICATION = Counter("oncall_recovery_verification_total", "Recovery verification outcomes.", ("passed",))
APPROVAL_WAIT_SECONDS = Histogram("oncall_approval_wait_seconds", "Time from plan creation to approval.")
