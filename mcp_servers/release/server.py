"""Read-only release facts backed by one fixed runtime JSON file."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common import BearerAuthMiddleware
from oncall.mcp_gateway.schemas import (
    CurrentReleaseRequest,
    CurrentReleaseResult,
    RecentReleasesRequest,
    RecentReleasesResult,
    ReleaseDiffRequest,
    ReleaseDiffResult,
    ReleaseRecord,
)


RELEASE_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "demo-release.json"
DEMO_SECRET = "demo-only-release-mcp-secret"


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "production"] = "local"
    release_mcp_secret: SecretStr = Field(default=SecretStr(DEMO_SECRET), min_length=16)

    @model_validator(mode="after")
    def reject_demo_production_secret(self) -> "ServerSettings":
        if (
            self.app_env == "production"
            and self.release_mcp_secret.get_secret_value() == DEMO_SECRET
        ):
            raise ValueError("production requires a non-demo Release MCP secret")
        return self


SERVER_SETTINGS = ServerSettings()
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    "oncall-release",
    instructions=(
        "Read-only release facts from a fixed runtime file. This server has no "
        "deployment, rollback, filesystem-path, command, or URL parameter."
    ),
    stateless_http=True,
    json_response=True,
)


def _load_scope(request: Any) -> dict[str, Any]:
    try:
        with RELEASE_PATH.open("rb") as release_file:
            if os.fstat(release_file.fileno()).st_size > 262_144:
                raise ValueError("release state exceeds 256 KiB")
            raw = release_file.read(262_145)
        if len(raw) > 262_144:
            raise ValueError("release state exceeds 256 KiB")
        payload = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("NEED_HUMAN: release facts are unavailable") from exc
    scope = payload.get("scope", {})
    requested = (request.project_id, request.environment, request.service)
    registered = (
        scope.get("project_id"),
        scope.get("environment"),
        scope.get("service"),
    )
    if requested != registered:
        raise ValueError("scope is not registered on this release server")
    return payload


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_current_release(request: CurrentReleaseRequest) -> CurrentReleaseResult:
    """Return the current release for one registered project scope."""

    payload = _load_scope(request)
    return CurrentReleaseResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        release=ReleaseRecord.model_validate(payload["current"]),
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_recent_releases(request: RecentReleasesRequest) -> RecentReleasesResult:
    """Return bounded release history from a required UTC time window."""

    payload = _load_scope(request)
    releases = [ReleaseRecord.model_validate(item) for item in payload.get("recent", [])]
    releases = [
        release
        for release in releases
        if request.start_time <= release.released_at <= request.end_time
    ]
    releases.sort(key=lambda item: item.released_at, reverse=True)
    return RecentReleasesResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        releases=releases[: request.limit],
    )


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def get_release_diff(request: ReleaseDiffRequest) -> ReleaseDiffResult:
    """Return a checked-in summary for a specific known version pair."""

    payload = _load_scope(request)
    key = f"{request.from_version}..{request.to_version}"
    summary = payload.get("diffs", {}).get(key)
    if summary is None:
        raise ValueError("release diff is not recorded for this version pair")
    return ReleaseDiffResult(
        project_id=request.project_id,
        environment=request.environment,
        service=request.service,
        from_version=request.from_version,
        to_version=request.to_version,
        summary=str(summary),
    )


@mcp.custom_route("/health/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "release-mcp"})


app = BearerAuthMiddleware(
    mcp.streamable_http_app(),
    SERVER_SETTINGS.release_mcp_secret.get_secret_value(),
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18082)
