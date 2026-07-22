"""Strict schemas for Git-managed investigation skills."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SkillToolName = Literal[
    "query_metrics",
    "query_service_logs",
    "get_service_health",
    "get_active_alerts",
    "get_current_release",
    "get_recent_releases",
    "get_release_diff",
]


class JsonSchemaArrayItems(BaseModel):
    """Closed scalar item schema for bounded Skill result arrays."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "number", "integer", "boolean", "object"]


class JsonSchemaProperty(BaseModel):
    """Small JSON Schema subset accepted for Skill structured output."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "number", "integer", "boolean", "array", "object"]
    description: str = Field(min_length=1, max_length=512)
    items: JsonSchemaArrayItems | None = None

    @model_validator(mode="after")
    def validate_array_items(self) -> "JsonSchemaProperty":
        if self.type == "array" and not self.items:
            raise ValueError("array properties must declare items")
        if self.type != "array" and self.items is not None:
            raise ValueError("items is only valid for array properties")
        return self


class SkillOutputSchema(BaseModel):
    """Closed object schema used to validate an Agent's Skill result."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["object"] = "object"
    properties: dict[str, JsonSchemaProperty] = Field(min_length=1)
    required: tuple[str, ...] = Field(min_length=1)
    additional_properties: Literal[False] = Field(
        default=False,
        alias="additionalProperties",
    )

    @model_validator(mode="after")
    def validate_required_properties(self) -> "SkillOutputSchema":
        unknown = set(self.required) - set(self.properties)
        if unknown:
            raise ValueError(f"required properties are not declared: {sorted(unknown)}")
        return self


class SkillTrigger(BaseModel):
    """Deterministic candidate filters; all declared dimensions must match."""

    model_config = ConfigDict(extra="forbid")

    alert_names: tuple[str, ...] = Field(min_length=1)
    service_types: tuple[str, ...] = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("alert_names", "service_types")
    @classmethod
    def reject_blank_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("trigger values cannot be blank")
        return values

    @field_validator("labels")
    @classmethod
    def reject_blank_labels(cls, labels: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not value.strip() for key, value in labels.items()):
            raise ValueError("trigger label names and values cannot be blank")
        return labels


class SkillManifest(BaseModel):
    """Validated representation of one ``skill.yaml`` file."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    version: str = Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    description: str = Field(min_length=1, max_length=1024)
    triggers: SkillTrigger
    allowed_tools: tuple[SkillToolName, ...] = Field(min_length=1)
    forbidden_actions: tuple[str, ...] = Field(min_length=1)
    output_schema: SkillOutputSchema

    @field_validator("allowed_tools")
    @classmethod
    def require_unique_tools(
        cls, tools: tuple[SkillToolName, ...]
    ) -> tuple[SkillToolName, ...]:
        if len(tools) != len(set(tools)):
            raise ValueError("allowed_tools cannot contain duplicates")
        return tools

    @field_validator("forbidden_actions")
    @classmethod
    def require_safety_prohibitions(cls, actions: tuple[str, ...]) -> tuple[str, ...]:
        normalized = {action.strip().lower() for action in actions}
        if any(not action for action in normalized):
            raise ValueError("forbidden_actions cannot contain blank values")
        required = {"rollback", "restart", "scale", "arbitrary_write"}
        missing = required - normalized
        if missing:
            raise ValueError(
                "forbidden_actions must explicitly prohibit " + ", ".join(sorted(missing))
            )
        return actions


class SkillDefinition(SkillManifest):
    """A manifest plus trusted instructions loaded from the same package."""

    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    instructions: str = Field(min_length=1, max_length=50_000)
    source_path: str
