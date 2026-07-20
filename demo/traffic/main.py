"""Continuously generate predictable request traffic for the demo service."""

import logging
import os
import time

import httpx


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)
TARGET_URL = os.getenv("DEMO_URL", "http://demo-service:8080/api/orders")


def main() -> None:
    """Request the order API once per second without stopping on errors."""

    with httpx.Client(timeout=2.0) as client:
        while True:
            try:
                response = client.get(TARGET_URL)
                if response.is_error:
                    LOGGER.error("demo_request_failed", extra={"status_code": response.status_code})
            except httpx.HTTPError as exc:
                LOGGER.error("demo_request_error", extra={"error": str(exc)})
            time.sleep(1)


if __name__ == "__main__":
    main()
