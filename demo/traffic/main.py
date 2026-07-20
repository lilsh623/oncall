"""Continuously generate predictable request traffic for the demo service."""

import logging
import os
import time

import httpx


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)
TARGET_URL = os.getenv("DEMO_URL", "http://demo-service:8080/api/orders")
REQUEST_PERIOD_SECONDS = 1.0


def next_request_deadline(previous_deadline: float, now: float) -> float:
    """Advance one period, avoiding catch-up bursts after a slow request."""

    scheduled_deadline = previous_deadline + REQUEST_PERIOD_SECONDS
    if scheduled_deadline <= now:
        return now + REQUEST_PERIOD_SECONDS
    return scheduled_deadline


def main() -> None:
    """Request the order API once per second without stopping on errors."""

    with httpx.Client(timeout=2.0) as client:
        deadline = time.monotonic()
        while True:
            try:
                response = client.get(TARGET_URL)
                if response.is_error:
                    LOGGER.error("demo_request_failed", extra={"status_code": response.status_code})
            except httpx.HTTPError as exc:
                LOGGER.error("demo_request_error", extra={"error": str(exc)})
            now = time.monotonic()
            deadline = next_request_deadline(deadline, now)
            time.sleep(deadline - now)


if __name__ == "__main__":
    main()
