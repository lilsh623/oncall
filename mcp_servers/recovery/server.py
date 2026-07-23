"""Recovery MCP exposing only the approved rollback_release write tool."""

from __future__ import annotations

import hmac
import sqlite3
import time
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common import BearerAuthMiddleware
from mcp_servers.recovery.podman_runner import rollback_release as run_rollback_release


DEMO_SECRET = "demo-only-recovery-mcp-secret"
DEMO_APPROVAL_SECRET = "demo-only-recovery-approval-secret"
NONCE_DB_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "recovery-nonces.sqlite3"


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "production"] = "local"
    recovery_mcp_secret: SecretStr = Field(default=SecretStr(DEMO_SECRET), min_length=16)
    recovery_approval_secret: SecretStr = Field(
        default=SecretStr(DEMO_APPROVAL_SECRET), min_length=16
    )

    @model_validator(mode="after")
    def reject_demo_production_secret(self) -> "ServerSettings":
        if self.app_env == "production" and (
            self.recovery_mcp_secret.get_secret_value() == DEMO_SECRET
            or self.recovery_approval_secret.get_secret_value() == DEMO_APPROVAL_SECRET
        ):
            raise ValueError("production requires non-demo Recovery MCP secrets")
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
)


class RollbackReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Literal["demo-shop"]
    environment: Literal["staging"]
    service: Literal["order-api"]
    current_version: Literal["v2"]
    target_version: Literal["v1"]
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


def _claim_nonce(nonce: str, expires_at: int) -> bool:
    """Durably reject replays even if the Recovery MCP process is restarted."""

    NONCE_DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(NONCE_DB_PATH, timeout=3.0, isolation_level=None) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS consumed_nonces "
            "(nonce TEXT PRIMARY KEY, expires_at INTEGER NOT NULL)"
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM consumed_nonces WHERE expires_at < ?", (int(time.time()),))
            connection.execute(
                "INSERT INTO consumed_nonces (nonce, expires_at) VALUES (?, ?)",
                (nonce, expires_at),
            )
        except sqlite3.IntegrityError:
            connection.execute("ROLLBACK")
            return False
        connection.execute("COMMIT")
    return True


def verify_approval_proof(request: RollbackReleaseRequest, secret: str | None = None) -> None:
    now = int(time.time())
    if abs(now - request.issued_at) > 300:
        raise ValueError("approval proof expired")
    key = (secret or SERVER_SETTINGS.recovery_approval_secret.get_secret_value()).encode("utf-8")
    expected = hmac.new(key, _proof_payload(request).encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(expected, request.approval_proof):
        raise ValueError("approval proof mismatch")
    if not _claim_nonce(request.nonce, request.issued_at + 300):
        raise ValueError("approval proof nonce replayed")


@mcp.tool(annotations=WRITE_TOOL, structured_output=True)
def rollback_release(request: RollbackReleaseRequest) -> dict[str, object]:
    """Validate approval proof and execute the fixed v2 -> v1 rollback."""

    verify_approval_proof(request)
    result = run_rollback_release(
        request.target_version,
        expected_current_version=request.current_version,
    )
    return {"status": "succeeded" if result["returncode"] == 0 else "failed", "runner": result}


@mcp.custom_route("/health/ready", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "oncall-recovery-mcp"})


app = mcp.streamable_http_app()
app.add_middleware(
    BearerAuthMiddleware,
    expected_token=SERVER_SETTINGS.recovery_mcp_secret.get_secret_value(),
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
