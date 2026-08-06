"""Public contracts for the operations overview."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertIntegrationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = "tencent_monitor"
    project_id: str
    webhook_path: str


class OperationsOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automation_state: str
    open_incidents: int
    processing_incidents: int
    waiting_approval: int
    needs_human: int
    firing_alerts: int
    alert_events_24h: int
    resolved_incidents_24h: int
    latest_alert_at: datetime | None = None
    conversation_live: bool
    integrations: list[AlertIntegrationSummary]
