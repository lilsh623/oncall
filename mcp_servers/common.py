"""ASGI authentication shared by the narrow MCP servers."""

from __future__ import annotations

import hmac
from collections.abc import Callable


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
