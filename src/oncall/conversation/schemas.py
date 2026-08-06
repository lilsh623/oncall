"""Public contracts for conversations and routing decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


ConversationIntent = Literal["KNOWLEDGE", "INCIDENT"]
IncidentAction = Literal["NONE", "QUERY", "INVESTIGATE", "PLAN", "APPROVAL_BLOCKED"]


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: ConversationIntent
    action: IncidentAction = "NONE"
    incident_id: UUID | None = None
    project_id: str | None = None
    environment: str | None = None
    service: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    rationale: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def validate_intent_action(self) -> "RouterDecision":
        if self.intent == "KNOWLEDGE" and self.action != "NONE":
            raise ValueError("KNOWLEDGE intent only allows NONE")
        if self.intent == "INCIDENT" and self.action == "NONE":
            raise ValueError("INCIDENT intent requires a bounded action")
        return self


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=256)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)


class MessageView(BaseModel):
    id: UUID
    role: str
    content: str
    intent: str | None
    action: str | None
    entities: dict[str, Any]
    citations: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: datetime


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    status: str
    last_intent: str | None
    linked_incident_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageView]


class TurnResponse(BaseModel):
    conversation: ConversationSummary
    user_message: MessageView
    assistant_message: MessageView
    route: RouterDecision
    memory_used: int = 0
    enqueued: bool = False
