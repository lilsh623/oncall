"""Stable identity generation for alerts."""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_VENDOR_FINGERPRINT = re.compile(r"^[0-9a-fA-F]{16,64}$")


def valid_source_fingerprint(value: object) -> str | None:
    """Return a normalized vendor fingerprint when it is well formed."""

    if not isinstance(value, str) or not _VENDOR_FINGERPRINT.fullmatch(value):
        return None
    return value.lower()


def stable_fingerprint(
    *,
    project_id: str,
    environment: str,
    service: str,
    alert_name: str,
    labels: Mapping[str, Any],
    stable_label_names: Sequence[str] = (),
) -> str:
    """Build a replay-stable SHA-256 identity from explicitly stable facts."""

    stable_labels = {
        name: labels[name]
        for name in sorted(set(stable_label_names))
        if name in labels
    }
    identity = {
        "alert_name": alert_name,
        "environment": environment,
        "labels": stable_labels,
        "project_id": project_id,
        "service": service,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def resolve_fingerprint(
    source_fingerprint: object,
    *,
    project_id: str,
    environment: str,
    service: str,
    alert_name: str,
    labels: Mapping[str, Any],
    stable_label_names: Sequence[str] = (),
) -> str:
    """Prefer the source identity and deterministically generate a fallback."""

    official = valid_source_fingerprint(source_fingerprint)
    if official is not None:
        return official
    return stable_fingerprint(
        project_id=project_id,
        environment=environment,
        service=service,
        alert_name=alert_name,
        labels=labels,
        stable_label_names=stable_label_names,
    )
