"""Pure, deterministic helpers for grounded experience extraction."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


REDACTION_VERSION = "1.0"
_SENSITIVE_KEYS = re.compile(
    r"(authorization|cookie|password|secret|token|api[_-]?key|connection[_-]?string)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_URL_CREDENTIAL = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)


def redact_text(value: str) -> str:
    """Remove common credentials and personal/network identifiers from reusable text."""

    value = _BEARER.sub("Bearer <SECRET>", value)
    value = _EMAIL.sub("<USER_EMAIL>", value)
    value = _IPV4.sub("<INTERNAL_IP>", value)
    return _URL_CREDENTIAL.sub(r"\g<scheme><SECRET>@", value)


def redact_value(value: Any) -> Any:
    """Recursively redact structured values without changing their shape."""

    if isinstance(value, dict):
        return {
            str(key): "<REDACTED>" if _SENSITIVE_KEYS.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def pattern_fingerprint(
    *,
    project_id: str,
    environment: str,
    service: str,
    root_cause: str,
    action_type: str,
) -> str:
    """Identify an exact reusable pattern without tying it to one Incident ID."""

    normalized = {
        "project_id": project_id.strip().lower(),
        "environment": environment.strip().lower(),
        "service": service.strip().lower(),
        "root_cause": " ".join(root_cause.lower().split()),
        "action_type": action_type.strip().lower(),
    }
    return canonical_hash(normalized)


def candidate_document(
    *,
    title: str,
    summary: str,
    symptoms: list[str],
    root_cause: str,
    action: dict[str, Any],
    verification: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    """Return the exact redacted document whose hash is approved and published."""

    return redact_value(
        {
            "title": title,
            "summary": summary,
            "symptoms": symptoms,
            "root_cause": root_cause,
            "action": action,
            "verification": verification,
            "warnings": warnings,
        }
    )
