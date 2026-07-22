"""ASGI authentication shared by the narrow MCP servers."""

from __future__ import annotations

from collections.abc import Callable
import hmac
import json
from typing import Any

import httpx


class BoundedJsonError(ValueError):
    """Raised when an upstream JSON body is invalid or exceeds its contract."""


async def get_bounded_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    params: dict[str, Any] | None = None,
) -> Any:
    """Stream decoded bytes, enforce a limit, then parse JSON exactly once."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    body = bytearray()
    async with client.stream("GET", url, params=params) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > max_bytes:
                raise BoundedJsonError("upstream JSON response exceeds its byte limit")
            body.extend(chunk)
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundedJsonError("upstream response is not valid JSON") from exc


class BearerAuthMiddleware:
    """Require one operator-configured service secret on the MCP endpoint."""

    def __init__(self, app: Callable, secret: str) -> None:
        self.app = app
        self._expected = f"Bearer {secret}"

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"").decode("utf-8", "replace")
            if not hmac.compare_digest(supplied, self._expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"detail":"invalid MCP service credential"}',
                    }
                )
                return
        await self.app(scope, receive, send)
