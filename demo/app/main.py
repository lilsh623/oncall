"""Small order API used to demonstrate a release-regression alert."""

import os
import random

from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, make_asgi_app


app = FastAPI(title="Demo Order API")
REQUESTS = Counter(
    "demo_http_requests_total",
    "Requests to the demo order endpoint.",
    ("status", "version"),
)


def demo_version() -> str:
    """Return the version selected for this container instance."""

    return os.getenv("DEMO_VERSION", "v1")


def failure_rate() -> float:
    """Read a safe, bounded failure rate from the environment."""

    try:
        return min(1.0, max(0.0, float(os.getenv("DEMO_FAILURE_RATE", "1.0"))))
    except ValueError:
        return 1.0


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "order-api"}


@app.get("/version")
async def version() -> dict[str, str]:
    return {"service": "order-api", "version": demo_version()}


@app.get("/api/orders")
async def orders() -> dict[str, object]:
    version = demo_version()
    if version == "v2" and random.random() < failure_rate():
        REQUESTS.labels(status="500", version=version).inc()
        raise HTTPException(status_code=500, detail="simulated order API failure")

    REQUESTS.labels(status="200", version=version).inc()
    return {"orders": [{"id": "order-1001", "status": "created"}]}


app.mount("/metrics", make_asgi_app())
