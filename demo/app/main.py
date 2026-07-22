"""Small order API used to demonstrate a release-regression alert."""

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import random

from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, make_asgi_app


LOG_PATH = Path(os.getenv("DEMO_LOG_PATH", "/tmp/oncall-demo-service.log"))
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


class JsonLogFormatter(logging.Formatter):
    """Serialize bounded demo request facts as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "order-api",
            "message": record.getMessage()[:512],
            "status_code": getattr(record, "status_code", None),
            "version": getattr(record, "version", None),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


LOGGER = logging.getLogger("demo.order-api")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
if not LOGGER.handlers:
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=1_048_576,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLogFormatter())
    LOGGER.addHandler(handler)


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
        LOGGER.error(
            "order_request_failed",
            extra={"status_code": 500, "version": version},
        )
        raise HTTPException(status_code=500, detail="simulated order API failure")

    REQUESTS.labels(status="200", version=version).inc()
    LOGGER.info(
        "order_request_succeeded",
        extra={"status_code": 200, "version": version},
    )
    return {"orders": [{"id": "order-1001", "status": "created"}]}


app.mount("/metrics", make_asgi_app())
