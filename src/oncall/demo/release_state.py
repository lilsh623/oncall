"""Safely synchronize fixed demo release facts after a verified version switch."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RELEASE_STATE_PATH = REPOSITORY_ROOT / ".runtime" / "demo-release.json"
VERSION_ENDPOINT = "http://127.0.0.1:18080/version"
MAX_STATE_BYTES = 262_144
MAX_HISTORY_ENTRIES = 50


class DemoVersion(StrEnum):
    V1 = "v1"
    V2 = "v2"


class DemoReleaseSyncError(RuntimeError):
    """Raised before state mutation when the observed demo version is unsafe."""


def _read_release_state() -> dict[str, Any]:
    try:
        with RELEASE_STATE_PATH.open("rb") as state_file:
            size = os.fstat(state_file.fileno()).st_size
            if size > MAX_STATE_BYTES:
                raise DemoReleaseSyncError("demo release state exceeds 256 KiB")
            raw = state_file.read(MAX_STATE_BYTES + 1)
    except OSError as exc:
        raise DemoReleaseSyncError("demo release state is unavailable") from exc
    if len(raw) > MAX_STATE_BYTES:
        raise DemoReleaseSyncError("demo release state exceeds 256 KiB")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DemoReleaseSyncError("demo release state is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise DemoReleaseSyncError("demo release state must be an object")
    return payload


def _fetch_observed_version() -> DemoVersion:
    try:
        with httpx.Client(timeout=2.0) as client:
            with client.stream("GET", VERSION_ENDPOINT) as response:
                response.raise_for_status()
                raw = b""
                for chunk in response.iter_bytes():
                    if len(raw) + len(chunk) > 4096:
                        raise DemoReleaseSyncError("demo version response exceeds 4 KiB")
                    raw += chunk
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DemoReleaseSyncError("demo version response must be an object")
        if payload.get("service") != "order-api":
            raise DemoReleaseSyncError("demo version response has the wrong service")
        return DemoVersion(payload.get("version"))
    except DemoReleaseSyncError:
        raise
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise DemoReleaseSyncError("demo version endpoint is not ready") from exc


def _wait_for_version(expected: DemoVersion, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_observed: DemoVersion | None = None
    while time.monotonic() < deadline:
        try:
            last_observed = _fetch_observed_version()
            if last_observed == expected:
                return
        except DemoReleaseSyncError:
            pass
        time.sleep(0.5)
    observed = last_observed.value if last_observed else "unavailable"
    raise DemoReleaseSyncError(
        f"expected demo version {expected.value}, observed {observed}; state was not changed"
    )


def _atomic_write_state(payload: dict[str, Any]) -> None:
    RELEASE_STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(serialized) > MAX_STATE_BYTES:
        raise DemoReleaseSyncError("updated demo release state exceeds 256 KiB")
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=RELEASE_STATE_PATH.parent,
            prefix=".demo-release-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(serialized)
            os.fchmod(temporary.fileno(), 0o600)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, RELEASE_STATE_PATH)
        temporary_path = None
        directory_fd = os.open(RELEASE_STATE_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def synchronize_release_state(version: DemoVersion) -> bool:
    """Verify the fixed endpoint, then atomically record a bounded release event."""

    version = DemoVersion(version)
    _wait_for_version(version)
    payload = _read_release_state()
    expected_scope = {
        "project_id": "demo-shop",
        "environment": "staging",
        "service": "order-api",
    }
    if payload.get("scope") != expected_scope:
        raise DemoReleaseSyncError("demo release state has an unexpected scope")
    current = payload.get("current")
    if not isinstance(current, dict):
        raise DemoReleaseSyncError("demo release state has no current release")
    try:
        current_version = DemoVersion(current.get("version"))
    except ValueError as exc:
        raise DemoReleaseSyncError("demo release state has an invalid current version") from exc
    if current_version == version:
        return False

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    action = "stable release restored" if version == DemoVersion.V1 else "demo regression enabled"
    record = {
        "version": version.value,
        "released_at": timestamp,
        "released_by": "operator:demo-switch",
        "change_summary": action,
    }
    recent = payload.get("recent", [])
    if not isinstance(recent, list):
        raise DemoReleaseSyncError("demo release history must be a list")
    payload["current"] = record
    payload["recent"] = [record, *recent][:MAX_HISTORY_ENTRIES]
    if _fetch_observed_version() != version:
        raise DemoReleaseSyncError("demo version changed before state commit")
    _atomic_write_state(payload)
    return True
