"""FastAPI application factory and operational endpoints."""

from contextlib import asynccontextmanager
from time import perf_counter
from typing import AsyncIterator
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

from oncall.auth.admin_router import router as admin_router
from oncall.auth.router import router as auth_router
from oncall.alerts.router import router as alerts_router
from oncall.logging import configure_logging
from oncall.skills.registry import get_skill_registry


HTTP_REQUESTS_TOTAL = Counter(
    "oncall_http_requests_total",
    "Total HTTP requests handled by the OnCall API.",
    ("method", "path", "status_code"),
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "oncall_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "path"),
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request identifier to structured logs for each HTTP request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started_at = perf_counter()

        try:
            response = await call_next(request)
            elapsed = perf_counter() - started_at
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=request.method,
                path=request.url.path,
            ).observe(elapsed)
            structlog.get_logger(__name__).info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_seconds=elapsed,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure application services around the FastAPI lifespan."""

    configure_logging()
    get_skill_registry().load()
    logger = structlog.get_logger(__name__)
    logger.info("application_started")
    yield
    logger.info("application_stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(title="OnCall API", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(alerts_router)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok", "service": "oncall-api"}

    @app.get("/health/ready")
    async def ready() -> dict[str, str]:
        return {"status": "ok", "service": "oncall-api"}

    return app
