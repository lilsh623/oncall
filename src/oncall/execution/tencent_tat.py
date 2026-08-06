"""Tencent Cloud TAT adapter used by the write-capable Recovery MCP."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PARAMETER_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class TencentTatSettings(BaseSettings):
    """Tencent Cloud credentials owned by the isolated Recovery MCP process."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tencentcloud_secret_id: SecretStr | None = None
    tencentcloud_secret_key: SecretStr | None = None
    tencentcloud_api_base_host: str | None = None

    @model_validator(mode="after")
    def require_credential_pair(self) -> "TencentTatSettings":
        if (self.tencentcloud_secret_id is None) != (self.tencentcloud_secret_key is None):
            raise ValueError("Tencent Cloud SecretId and SecretKey must be configured together")
        return self


@lru_cache
def get_tencent_tat_settings() -> TencentTatSettings:
    return TencentTatSettings()


class TencentTatConfig(BaseModel):
    """A fixed Tencent Cloud command and target set for one managed service."""

    model_config = ConfigDict(extra="forbid")

    region: str = Field(pattern=r"^[a-z]+-[a-z]+\d*$", max_length=64)
    command_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,128}$")
    instance_ids: list[str] = Field(min_length=1, max_length=200)
    parameters: dict[str, str] = Field(default_factory=dict)
    target_version_parameter: str = Field(
        default="target_version", pattern=PARAMETER_NAME_PATTERN
    )
    current_version_parameter: str | None = Field(
        default="current_version", pattern=PARAMETER_NAME_PATTERN
    )
    username: str | None = Field(default=None, min_length=1, max_length=256)
    working_directory: str | None = Field(default=None, min_length=1, max_length=1024)
    command_timeout_seconds: int | None = Field(default=None, ge=1, le=86400)
    poll_timeout_seconds: int = Field(default=55, ge=5, le=60)
    poll_interval_seconds: float = Field(default=2.0, ge=0.5, le=10.0)

    @field_validator("parameters")
    @classmethod
    def validate_parameter_names(cls, value: dict[str, str]) -> dict[str, str]:
        for name in value:
            if not re.fullmatch(PARAMETER_NAME_PATTERN, name):
                raise ValueError("TAT parameter names must contain only letters, numbers, _ or -")
        return value


class TencentTatError(RuntimeError):
    """A non-sensitive failure returned by Tencent Cloud TAT."""


class TencentTatApi(Protocol):
    def invoke_command(self, payload: dict[str, Any]) -> str: ...

    def describe_invocation(self, invocation_id: str) -> str | None: ...


class _SdkTencentTatApi:
    def __init__(self, region: str) -> None:
        try:
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.tat.v20201028 import tat_client
            from tencentcloud.tat.v20201028 import models
        except ImportError as exc:
            raise TencentTatError("Tencent Cloud TAT SDK is not installed") from exc

        settings = get_tencent_tat_settings()
        secret_id = settings.tencentcloud_secret_id
        secret_key = settings.tencentcloud_secret_key
        if (
            secret_id is None
            or secret_key is None
            or not secret_id.get_secret_value()
            or not secret_key.get_secret_value()
        ):
            raise TencentTatError("Tencent Cloud TAT credentials are not configured")

        http_profile = HttpProfile()
        if settings.tencentcloud_api_base_host:
            http_profile.endpoint = settings.tencentcloud_api_base_host
        profile = ClientProfile()
        profile.httpProfile = http_profile
        self._models = models
        self._client = tat_client.TatClient(
            credential.Credential(
                secret_id.get_secret_value(), secret_key.get_secret_value()
            ),
            region,
            profile,
        )

    def invoke_command(self, payload: dict[str, Any]) -> str:
        request = self._models.InvokeCommandRequest()
        request.from_json_string(json.dumps(payload))
        response = self._client.InvokeCommand(request)
        if not response.InvocationId:
            raise TencentTatError("Tencent Cloud TAT returned no invocation id")
        return str(response.InvocationId)

    def describe_invocation(self, invocation_id: str) -> str | None:
        request = self._models.DescribeInvocationsRequest()
        request.from_json_string(
            json.dumps({"InvocationIds": [invocation_id], "Limit": 1, "Offset": 0})
        )
        response = self._client.DescribeInvocations(request)
        invocations = response.InvocationSet or []
        if not invocations:
            return None
        return str(invocations[0].InvocationStatus)


def _command_payload(request: Any, config: TencentTatConfig) -> dict[str, Any]:
    parameters = dict(config.parameters)
    parameters[config.target_version_parameter] = str(request.target_version)
    if config.current_version_parameter is not None:
        parameters[config.current_version_parameter] = str(request.current_version)
    payload: dict[str, Any] = {
        "CommandId": config.command_id,
        "InstanceIds": config.instance_ids,
        "Parameters": json.dumps(parameters, ensure_ascii=False, separators=(",", ":")),
    }
    if config.username:
        payload["Username"] = config.username
    if config.working_directory:
        payload["WorkingDirectory"] = config.working_directory
    if config.command_timeout_seconds is not None:
        payload["Timeout"] = config.command_timeout_seconds
    return payload


def invoke_tencent_tat_rollback(
    request: Any,
    config: TencentTatConfig,
    *,
    api: TencentTatApi | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Invoke a pre-registered TAT rollback command and wait for its terminal status."""

    client = api or _SdkTencentTatApi(config.region)
    try:
        invocation_id = client.invoke_command(_command_payload(request, config))
        deadline = monotonic() + config.poll_timeout_seconds
        status: str | None = None
        while monotonic() < deadline:
            status = client.describe_invocation(invocation_id)
            if status == "SUCCESS":
                return {
                    "status": "succeeded",
                    "provider": "tencent_tat",
                    "invocation_id": invocation_id,
                    "invocation_status": status,
                    "instance_ids": config.instance_ids,
                }
            if status in {
                "FAILED",
                "TIMEOUT",
                "CANCELLED",
                "PARTIAL_FAILED",
                "PARTIAL_CANCELLED",
            }:
                return {
                    "status": "failed",
                    "provider": "tencent_tat",
                    "invocation_id": invocation_id,
                    "invocation_status": status,
                    "instance_ids": config.instance_ids,
                }
            sleep(config.poll_interval_seconds)
    except TencentTatError:
        raise
    except Exception as exc:
        raise TencentTatError("Tencent Cloud TAT request failed") from exc
    return {
        "status": "failed",
        "provider": "tencent_tat",
        "invocation_id": invocation_id,
        "invocation_status": status or "PENDING",
        "instance_ids": config.instance_ids,
        "error": "Timed out while waiting for Tencent Cloud TAT.",
    }
