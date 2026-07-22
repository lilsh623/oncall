"""Deterministic keys and windows used for Incident correlation."""

import hashlib
from datetime import timedelta


INCIDENT_CORRELATION_WINDOW = timedelta(minutes=5)


def advisory_lock_key(*parts: str) -> int:
    """Map stable strings to PostgreSQL's signed 64-bit advisory-lock keyspace."""

    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
