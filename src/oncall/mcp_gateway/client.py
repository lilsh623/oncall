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
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from oncall.mcp_gateway.schemas import (
    FailureCategory,
    LogsResult,
    QueryLogsRequest,
    ToolAuditContext,
    ToolResult,
)
from oncall.database import async_session
from oncall.models import CloudProject, CloudService


class McpGatewaySettings(BaseSettings):
    """Independent server credentials and bounded transport settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "production"] = "local"
    # Official Tencent CLS MCP can run as a Streamable HTTP server. It normally
    # authenticates to Tencent with its own SecretId/SecretKey, so this inbound
    # token is intentionally optional.
    cls_mcp_url: AnyHttpUrl | None = None
    cls_mcp_auth_token: SecretStr | None = None
    mcp_timeout_seconds: float = Field(default=8.0, ge=0.5, le=30.0)
    mcp_max_result_bytes: int = Field(default=32_768, ge=4_096, le=262_144)


@dataclass(frozen=True)
class ToolSpec:
    request_model: type[BaseModel]


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "query_service_logs": ToolSpec(QueryLogsRequest),
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

    async def _configured_cls_service(
        self, project_id: str, environment: str, service: str
    ) -> CloudService | None:
        """Resolve the service's CLS binding without trusting model arguments."""

        async with async_session() as session:
            project = await session.scalar(
                select(CloudProject).where(CloudProject.project_id == project_id)
            )
            if project is None:
                return None
            return await session.scalar(
                select(CloudService).where(
                    CloudService.cloud_project_id == project.id,
                    CloudService.environment == environment,
                    CloudService.service == service,
                    CloudService.status == "active",
                )
            )

    @staticmethod
    def _structured_content_any(result: Any) -> Any:
        """Parse official MCP responses, which may be arrays instead of objects."""

        if result.structuredContent is not None:
            return result.structuredContent
        text = ""
        for item in result.content:
            fragment = getattr(item, "text", "")
            if len(text.encode("utf-8")) + len(fragment.encode("utf-8")) > 262_144:
                raise ValueError("MCP text result exceeds the pre-validation limit")
            text += fragment
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("MCP result has no structured content") from exc

    async def _call_cls_logs(
        self,
        request: QueryLogsRequest,
        *,
        tool_call_id: str,
        started_at: datetime,
        started_clock: float,
        audit: ToolAuditContext,
    ) -> ToolResult:
        service_binding = await self._configured_cls_service(
            request.project_id, request.environment, request.service
        )
        config = service_binding.observability_config if service_binding is not None else {}
        if config.get("provider") != "cls":
            raise LookupError("CLS is not configured for this service")
        if self.settings.cls_mcp_url is None:  # guarded by the public entry point
            raise AssertionError("CLS MCP URL is not configured")
        url = str(self.settings.cls_mcp_url)
        secret = (
            self.settings.cls_mcp_auth_token.get_secret_value()
            if self.settings.cls_mcp_auth_token is not None
            else ""
        )
        fields = config.get("log_fields") if isinstance(config.get("log_fields"), dict) else {}
        service_field = str(fields.get("service", "service"))
        level_field = str(fields.get("level", "level"))
        query_values = ",".join(f'"{level.value}"' for level in request.levels)
        # Identifiers are validated when the catalog is written. Values are
        # fixed enum values, so the model cannot inject arbitrary CQL.
        cql = f"{service_field}=\"{request.service}\" AND {level_field} IN ({query_values})"
        arguments = {
            "Region": str(config.get("region", "")),
            "TopicId": str(config.get("topic_id", "")),
            "From": round(request.start_time.timestamp() * 1000),
            "To": round(request.end_time.timestamp() * 1000),
            "Query": cql,
            "Sort": "desc",
            "Limit": request.limit,
            "Offset": 0,
        }
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        try:
            async with asyncio.timeout(self.settings.mcp_timeout_seconds):
                async with httpx.AsyncClient(headers=headers, timeout=self.settings.mcp_timeout_seconds) as http_client:
                    async with streamable_http_client(url, http_client=http_client) as (read_stream, write_stream, _):
                        async with ClientSession(
                            read_stream,
                            write_stream,
                            read_timeout_seconds=timedelta(seconds=self.settings.mcp_timeout_seconds),
                        ) as mcp_session:
                            await mcp_session.initialize()
                            result = await mcp_session.call_tool(
                                "SearchLog",
                                arguments,
                                meta={"tool_call_id": tool_call_id, "audit": audit.model_dump(mode="json")},
                            )
        except TimeoutError as exc:
            raise McpGatewayError(FailureCategory.RETRYABLE, "MCP_TIMEOUT", "CLS MCP call timed out", tool_name="query_service_logs", tool_call_id=tool_call_id) from exc
        except McpGatewayError:
            raise
        except Exception as exc:
            category, code = _transport_failure(exc)
            raise McpGatewayError(category, code, "CLS MCP transport failed", tool_name="query_service_logs", tool_call_id=tool_call_id) from exc
        if result.isError:
            raise McpGatewayError(FailureCategory.TERMINAL, "MCP_TOOL_ERROR", "CLS SearchLog failed", tool_name="query_service_logs", tool_call_id=tool_call_id)
        raw = self._structured_content_any(result)
        records: list[Any]
        if isinstance(raw, list):
            records = raw
        elif isinstance(raw, dict):
            records = raw.get("Results") or raw.get("results") or raw.get("Logs") or raw.get("logs") or raw.get("data") or []
            if isinstance(records, dict):
                records = records.get("Results") or records.get("results") or []
        else:
            records = []
        entries: list[dict[str, Any]] = []
        timestamp_field = str(fields.get("timestamp", "Time"))
        for record in records[:100]:
            if not isinstance(record, dict):
                continue
            log = record.get("LogJson", record.get("log_json", record))
            if isinstance(log, str):
                try:
                    log = json.loads(log)
                except json.JSONDecodeError:
                    log = {"message": log}
            if not isinstance(log, dict):
                log = {"message": str(log)}
            timestamp = record.get(timestamp_field, record.get("Time", log.get("timestamp")))
            parsed_time = datetime.fromtimestamp(float(timestamp) / (1000 if float(timestamp) > 10_000_000_000 else 1), tz=timezone.utc) if isinstance(timestamp, (int, float)) else datetime.now(timezone.utc)
            raw_level = str(log.get(fields.get("level", "level"), log.get("level", "INFO"))).upper()
            level = {"WARN": "WARNING", "WARNING": "WARNING", "ERR": "ERROR", "ERROR": "ERROR", "INFO": "INFO"}.get(raw_level, "INFO")
            entries.append({
                "timestamp": parsed_time,
                "level": level,
                "message": str(log.get(fields.get("message", "message"), log.get("message", "")))[:2048],
                "trace_id": str(log.get(fields.get("trace_id", "trace_id"))) if log.get(fields.get("trace_id", "trace_id")) is not None else None,
                "status_code": int(log.get(fields.get("status_code", "status_code"))) if str(log.get(fields.get("status_code", "status_code"), "")).isdigit() else None,
                "version": str(log.get(fields.get("version", "version"))) if log.get(fields.get("version", "version")) is not None else None,
            })
        response = LogsResult(
            project_id=request.project_id,
            environment=request.environment,
            service=request.service,
            entries=entries,
            returned_bytes=0,
            truncated=len(records) > len(entries),
            source_status="available",
        )
        response.returned_bytes = min(
            _json_size(response.model_dump(mode="json").get("entries", [])), 20_480
        )
        data, truncated = _bounded_result(response.model_dump(mode="json"), LogsResult, self.settings.mcp_max_result_bytes)
        return ToolResult(
            tool_call_id=tool_call_id,
            server="cls",
            tool_name="query_service_logs",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            duration_ms=max(0, round((perf_counter() - started_clock) * 1000)),
            audit=audit,
            truncated=truncated,
            data=data,
        )

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

        if self.settings.cls_mcp_url is None:
            raise McpGatewayError(
                FailureCategory.TERMINAL,
                "CLS_NOT_CONFIGURED",
                "CLS MCP is not configured",
                tool_name=name,
                tool_call_id=tool_call_id,
            )
        try:
            service_binding = await self._configured_cls_service(
                request.project_id, request.environment, request.service
            )
        except Exception:
            service_binding = None
        if (
            service_binding is None
            or not isinstance(service_binding.observability_config, dict)
            or service_binding.observability_config.get("provider") != "cls"
        ):
            raise McpGatewayError(
                FailureCategory.TERMINAL,
                "CLS_SERVICE_NOT_CONFIGURED",
                "CLS is not configured for this service",
                tool_name=name,
                tool_call_id=tool_call_id,
            )
        audit = ToolAuditContext(
            incident_id=incident_id,
            graph_run_id=graph_run_id,
            agent_name=agent_name,
        )
        return await self._call_cls_logs(
            request,
            tool_call_id=tool_call_id,
            started_at=started_at,
            started_clock=started_clock,
            audit=audit,
        )
