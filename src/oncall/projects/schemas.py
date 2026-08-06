"""Validated contracts for onboarded cloud projects and services."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from oncall.execution.tencent_tat import TencentTatConfig


SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"
FIELD_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ProjectCreate(StrictModel):
    project_id: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    provider: Literal["tencent"] = "tencent"
    region: str = Field(pattern=r"^[a-z]+-[a-z]+\d*$", max_length=64)


class ProjectResponse(StrictModel):
    id: UUID
    project_id: str
    name: str
    provider: str
    region: str
    status: str
    created_at: datetime
    updated_at: datetime


class ClsLogFields(StrictModel):
    service: str = Field(default="service", pattern=FIELD_PATTERN)
    level: str = Field(default="level", pattern=FIELD_PATTERN)
    message: str = Field(default="message", pattern=FIELD_PATTERN)
    trace_id: str = Field(default="trace_id", pattern=FIELD_PATTERN)
    status_code: str = Field(default="status_code", pattern=FIELD_PATTERN)
    version: str = Field(default="version", pattern=FIELD_PATTERN)


class ClsObservabilityConfig(StrictModel):
    provider: Literal["cls"] = "cls"
    region: str = Field(pattern=r"^[a-z]+-[a-z]+\d*$", max_length=64)
    topic_id: str = Field(min_length=1, max_length=128)
    log_fields: ClsLogFields = Field(default_factory=ClsLogFields)


class RecoveryConfig(StrictModel):
    provider: Literal["disabled", "tencent_tat"] = "disabled"
    tool_name: Literal["rollback_release"] = "rollback_release"
    action_policy: dict[Literal["rollback_release"], Literal["approval", "deny"]] = Field(
        default_factory=lambda: {"rollback_release": "approval"}
    )
    tencent_tat: TencentTatConfig | None = None

    @model_validator(mode="after")
    def require_tat_config(self) -> "RecoveryConfig":
        if self.provider == "tencent_tat" and self.tencent_tat is None:
            raise ValueError("tencent_tat provider requires a fixed TAT configuration")
        if self.provider == "disabled" and self.tencent_tat is not None:
            raise ValueError("disabled recovery cannot include Tencent TAT configuration")
        return self


class ServiceCreate(StrictModel):
    environment: str = Field(pattern=SLUG_PATTERN, max_length=64)
    service: str = Field(pattern=SLUG_PATTERN, max_length=128)
    runtime_type: Literal["tke", "cvm"]
    resource_config: dict[str, Any] = Field(default_factory=dict)
    observability_config: ClsObservabilityConfig
    recovery_config: RecoveryConfig = Field(default_factory=RecoveryConfig)
    health_config: dict[str, Any] = Field(default_factory=dict)


class ServiceResponse(StrictModel):
    id: UUID
    cloud_project_id: UUID
    environment: str
    service: str
    runtime_type: str
    resource_config: dict[str, Any]
    observability_config: dict[str, Any]
    recovery_config: dict[str, Any]
    health_config: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime


class IntegrationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=256)
    source: Literal["tencent_monitor", "tencent_cls"]
    environment: str = Field(pattern=SLUG_PATTERN, max_length=64)
    service: str = Field(pattern=SLUG_PATTERN, max_length=128)
    mapping_config: dict[str, Any] = Field(default_factory=dict)


class IntegrationResponse(StrictModel):
    id: UUID
    cloud_project_id: UUID
    integration_key: str
    name: str
    source: str
    environment: str
    service: str
    mapping_config: dict[str, Any]
    status: str
    last_received_at: datetime | None
    created_at: datetime
    updated_at: datetime
    webhook_path: str


class IntegrationCreated(IntegrationResponse):
    secret: str


class ProjectCatalogItem(ProjectResponse):
    services: list[ServiceResponse]
    integrations: list[IntegrationResponse]
