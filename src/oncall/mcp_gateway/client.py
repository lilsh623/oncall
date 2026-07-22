"""Schema-enforcing client for an explicit allowlist of read-only MCP tools."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from oncall.mcp_gateway.schemas import (
    ActiveAlertsRequest,
    ActiveAlertsResult,
    CurrentReleaseRequest,
    CurrentReleaseResult,
    FailureCategory,
    LogsResult,
    MetricsResult,
    QueryLogsRequest,
    QueryMetricsRequest,
    RecentReleasesRequest,
    RecentReleasesResult,
    ReleaseDiffRequest,
    ReleaseDiffResult,
    ServiceHealthRequest,
    ServiceHealthResult,
    ToolAuditContext,
    ToolResult,
)


class McpGatewaySettings(BaseSettings):
    """Independent server credentials and bounded transport settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "production"] = "local"
    observability_mcp_url: AnyHttpUrl = "http://127.0.0.1:18081/mcp"
    observability_mcp_secret: SecretStr = Field(
        default=SecretStr("demo-only-observability-mcp-secret"), min_length=16
    )
    release_mcp_url: AnyHttpUrl = "http://127.0.0.1:18082/mcp"
    release_mcp_secret: SecretStr = Field(
        default=SecretStr("demo-only-release-mcp-secret"), min_length=16
    )
    mcp_timeout_seconds: float = Field(default=8.0, ge=0.5, le=30.0)
    mcp_max_result_bytes: int = Field(default=32_768, ge=4_096, le=262_144)

    @model_validator(mode="after")
    def require_independent_secrets(self) -> "McpGatewaySettings":
        if (
            self.observability_mcp_secret.get_secret_value()
            == self.release_mcp_secret.get_secret_value()
        ):
            raise ValueError("each MCP server must use an independent service secret")
        if self.app_env == "production":
            demo_secrets = {
                "demo-only-observability-mcp-secret",
                "demo-only-release-mcp-secret",
            }
            configured = {
                self.observability_mcp_secret.get_secret_value(),
                self.release_mcp_secret.get_secret_value(),
            }
            if configured & demo_secrets:
                raise ValueError("production requires non-demo MCP service secrets")
        return self


@dataclass(frozen=True)
class ToolSpec:
    server: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "query_metrics": ToolSpec("observability", QueryMetricsRequest, MetricsResult),
    "query_service_logs": ToolSpec("observability", QueryLogsRequest, LogsResult),
    "get_service_health": ToolSpec(
        "observability", ServiceHealthRequest, ServiceHealthResult
    ),
    "get_active_alerts": ToolSpec("observability", ActiveAlertsRequest, ActiveAlertsResult),
    "get_current_release": ToolSpec("release", CurrentReleaseRequest, CurrentReleaseResult),
    "get_recent_releases": ToolSpec("release", RecentReleasesRequest, RecentReleasesResult),
    "get_release_diff": ToolSpec("release", ReleaseDiffRequest, ReleaseDiffResult),
}


class McpGatewayError(RuntimeError):
    """A safe failure classification suitable for deterministic graph routing."""

    def __init__(
        self,
        category: FailureCategory,
        code: str,
        message: str,
        *,
        tool_name: str,
        tool_call_id: str,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id


def _error_category(message: str) -> FailureCategory:
    upper = message.upper()
    if "RETRYABLE:" in upper:
        return FailureCategory.RETRYABLE
    if "NEED_HUMAN:" in upper:
        return FailureCategory.NEED_HUMAN
    return FailureCategory.TERMINAL


def _transport_failure(exc: Exception) -> tuple[FailureCategory, str]:
    """Classify transport failures without returning exception text to callers."""

    messages: list[str] = []

    def collect(error: BaseException) -> None:
        messages.append(str(error).lower())
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                collect(nested)

    collect(exc)
    combined = " ".join(messages)
    if any(marker in combined for marker in ("401", "403", "unauthorized", "forbidden")):
        return FailureCategory.TERMINAL, "MCP_AUTH_FAILED"
    return FailureCategory.RETRYABLE, "MCP_TRANSPORT_ERROR"


def _tool_error_message(result: Any) -> str:
    bounded = ""
    for item in result.content:
        text = getattr(item, "text", "")
        if not text:
            continue
        bounded = (bounded + " " + text)[:2048]
        if len(bounded) >= 2048:
            break
    return bounded or "MCP tool failed"


def _structured_content(result: Any) -> dict[str, Any]:
    if result.structuredContent is not None:
        return result.structuredContent
    text = ""
    for item in result.content:
        fragment = getattr(item, "text", "")
        if len(text.encode("utf-8")) + len(fragment.encode("utf-8")) > 262_144:
            raise ValueError("MCP text result exceeds the pre-validation limit")
        text += fragment
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("MCP result has no structured content") from exc
    if not isinstance(parsed, dict):
        raise ValueError("MCP result must be a JSON object")
    return parsed


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _shrink(value: Any, *, list_limit: int, string_limit: int) -> Any:
    if isinstance(value, dict):
        return {
            key: _shrink(item, list_limit=list_limit, string_limit=string_limit)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _shrink(item, list_limit=list_limit, string_limit=string_limit)
            for item in value[:list_limit]
        ]
    if isinstance(value, str) and len(value) > string_limit:
        return value[: max(1, string_limit - 1)] + "…"
    return value


def _bounded_result(
    value: dict[str, Any],
    response_model: type[BaseModel],
    max_bytes: int,
) -> tuple[dict[str, Any], bool]:
    if _json_size(value) <= max_bytes:
        return value, False
    for list_limit, string_limit in ((100, 2048), (50, 1024), (20, 512), (10, 256), (3, 128)):
        candidate = _shrink(value, list_limit=list_limit, string_limit=string_limit)
        validated = response_model.model_validate(candidate).model_dump(mode="json")
        if _json_size(validated) <= max_bytes:
            return validated, True
    raise ValueError("validated MCP output exceeds the Gateway result limit")


class McpGateway:
    """Call only registered read tools through server-specific authenticated clients."""

    def __init__(self, settings: McpGatewaySettings | None = None) -> None:
        self.settings = settings or McpGatewaySettings()

    def _server_config(self, server: str) -> tuple[str, str]:
        if server == "observability":
            return (
                str(self.settings.observability_mcp_url),
                self.settings.observability_mcp_secret.get_secret_value(),
            )
        if server == "release":
            return (
                str(self.settings.release_mcp_url),
                self.settings.release_mcp_secret.get_secret_value(),
            )
        raise AssertionError("Tool registry refers to an unknown MCP server")

    async def call_read_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        incident_id: str | None = None,
        graph_run_id: str | None = None,
        agent_name: str | None = None,
    ) -> ToolResult:
        """Validate, call, validate again, bound output, and attach audit metadata."""

        tool_call_id = str(uuid4())
        started_at = datetime.now(timezone.utc)
        started_clock = perf_counter()
        spec = TOOL_REGISTRY.get(name)
        if spec is None:
            raise McpGatewayError(
                FailureCategory.TERMINAL,
                "TOOL_NOT_ALLOWED",
                "tool is not registered as a read-only MCP capability",
                tool_name=name,
                tool_call_id=tool_call_id,
            )
        try:
            request = spec.request_model.model_validate(arguments)
        except ValidationError as exc:
            raise McpGatewayError(
                FailureCategory.TERMINAL,
                "INVALID_TOOL_ARGUMENTS",
                "tool arguments failed the registered schema",
                tool_name=name,
                tool_call_id=tool_call_id,
            ) from exc

        url, secret = self._server_config(spec.server)
        audit = ToolAuditContext(
            incident_id=incident_id,
            graph_run_id=graph_run_id,
            agent_name=agent_name,
        )
        try:
            async with asyncio.timeout(self.settings.mcp_timeout_seconds):
                async with httpx.AsyncClient(
                    headers={"Authorization": f"Bearer {secret}"},
                    timeout=self.settings.mcp_timeout_seconds,
                ) as http_client:
                    async with streamable_http_client(
                        url,
                        http_client=http_client,
                    ) as (read_stream, write_stream, _):
                        async with ClientSession(
                            read_stream,
                            write_stream,
                            read_timeout_seconds=timedelta(
                                seconds=self.settings.mcp_timeout_seconds
                            ),
                        ) as session:
                            await session.initialize()
                            result = await session.call_tool(
                                name,
                                {"request": request.model_dump(mode="json")},
                                meta={
                                    "tool_call_id": tool_call_id,
                                    "incident_id": incident_id,
                                    "graph_run_id": graph_run_id,
                                    "agent_name": agent_name,
                                },
                            )
        except TimeoutError as exc:
            raise McpGatewayError(
                FailureCategory.RETRYABLE,
                "MCP_TIMEOUT",
                "read-only MCP call timed out",
                tool_name=name,
                tool_call_id=tool_call_id,
            ) from exc
        except McpGatewayError:
            raise
        except Exception as exc:
            category, code = _transport_failure(exc)
            raise McpGatewayError(
                category,
                code,
                "read-only MCP transport failed",
                tool_name=name,
                tool_call_id=tool_call_id,
            ) from exc

        if result.isError:
            message = _tool_error_message(result)
            raise McpGatewayError(
                _error_category(message),
                "MCP_TOOL_ERROR",
                "read-only MCP tool failed",
                tool_name=name,
                tool_call_id=tool_call_id,
            )
        try:
            response = spec.response_model.model_validate(_structured_content(result))
            for scope_field in ("project_id", "environment", "service"):
                if getattr(response, scope_field) != getattr(request, scope_field):
                    raise ValueError(f"MCP output changed requested {scope_field}")
            validated = response.model_dump(mode="json")
            data, truncated = _bounded_result(
                validated,
                spec.response_model,
                self.settings.mcp_max_result_bytes,
            )
        except (ValidationError, ValueError) as exc:
            raise McpGatewayError(
                FailureCategory.TERMINAL,
                "INVALID_TOOL_OUTPUT",
                "MCP tool output failed the registered schema or size limit",
                tool_name=name,
                tool_call_id=tool_call_id,
            ) from exc

        completed_at = datetime.now(timezone.utc)
        result_envelope = ToolResult(
            tool_call_id=tool_call_id,
            server=spec.server,
            tool_name=name,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=max(0, round((perf_counter() - started_clock) * 1000)),
            audit=audit,
            truncated=truncated,
            data=data,
        )
        structlog.get_logger(__name__).info(
            "mcp_read_tool_completed",
            tool_call_id=tool_call_id,
            server=spec.server,
            tool_name=name,
            incident_id=incident_id,
            graph_run_id=graph_run_id,
            agent_name=agent_name,
            duration_ms=result_envelope.duration_ms,
            truncated=truncated,
        )
        return result_envelope
