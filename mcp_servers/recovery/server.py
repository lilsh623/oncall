"""Recovery MCP exposing only the approved rollback_release write tool."""

from __future__ import annotations

import asyncio
import hmac
import time
from collections.abc import Callable
from hashlib import sha256
from typing import Literal
from uuid import UUID

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis import Redis
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common import BearerAuthMiddleware
from oncall.execution.tencent_tat import (
    TencentTatConfig,
    TencentTatError,
    invoke_tencent_tat_rollback,
)


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "production"] = "local"
    recovery_mcp_secret: SecretStr = Field(min_length=16)
    recovery_approval_secret: SecretStr = Field(min_length=32)
    redis_url: str

    @model_validator(mode="after")
    def require_independent_secrets(self) -> "ServerSettings":
        if (
            self.recovery_mcp_secret.get_secret_value()
            == self.recovery_approval_secret.get_secret_value()
        ):
            raise ValueError("Recovery transport and approval secrets must be independent")
        return self

SERVER_SETTINGS = ServerSettings()
WRITE_TOOL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)

mcp = FastMCP(
    "oncall-recovery",
    instructions="Write-capable Recovery MCP. Only fixed rollback_release is exposed.",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "recovery-mcp:*"],
    ),
)


class RollbackReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    environment: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    service: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,127}$")
    current_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    target_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    action_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_id: UUID
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    issued_at: int
    approval_proof: str = Field(pattern=r"^[a-f0-9]{64}$")


def _proof_payload(request: RollbackReleaseRequest) -> str:
    return ":".join(
        [
            request.project_id,
            request.environment,
            request.service,
            request.current_version,
            request.target_version,
            request.action_plan_hash,
            str(request.approval_id),
            request.nonce,
            str(request.issued_at),
        ]
    )


def _claim_nonce(nonce: str) -> bool:
    try:
        client = Redis.from_url(SERVER_SETTINGS.redis_url, decode_responses=True)
        return bool(client.set(f"oncall:recovery:nonce:{nonce}", "1", nx=True, ex=600))
    except Exception as exc:
        raise ValueError("approval nonce store is unavailable") from exc


def verify_approval_proof(
    request: RollbackReleaseRequest,
    *,
    secret: str | None = None,
    claim_nonce: Callable[[str], bool] | None = None,
) -> None:
    """Verify freshness, HMAC integrity and one-time use before any TAT write."""

    if abs(int(time.time()) - request.issued_at) > 300:
        raise ValueError("approval proof expired")
    key = (
        secret
        if secret is not None
        else SERVER_SETTINGS.recovery_approval_secret.get_secret_value()
    ).encode("utf-8")
    expected = hmac.new(key, _proof_payload(request).encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(expected, request.approval_proof):
        raise ValueError("approval proof mismatch")
    if not (claim_nonce or _claim_nonce)(request.nonce):
        raise ValueError("approval proof nonce replayed")


@mcp.tool(annotations=WRITE_TOOL, structured_output=True)
async def rollback_release(
    request: RollbackReleaseRequest,
    tencent_tat: TencentTatConfig,
) -> dict[str, object]:
    """Execute an already approved rollback through Tencent Cloud TAT."""

    try:
        verify_approval_proof(request)
        return await asyncio.to_thread(invoke_tencent_tat_rollback, request, tencent_tat)
    except (TencentTatError, ValueError) as exc:
        return {"status": "failed", "provider": "tencent_tat", "error": str(exc)}


@mcp.custom_route("/health/ready", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "oncall-recovery-mcp"})


app = mcp.streamable_http_app()
app.add_middleware(
    BearerAuthMiddleware,
    secret=SERVER_SETTINGS.recovery_mcp_secret.get_secret_value(),
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
