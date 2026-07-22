"""Fixed Podman rollback runner for the demo service."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from typing import Literal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_VERSIONS = {"v1", "v2"}


def build_rollback_command(target_version: str) -> list[str]:
    """Build a fixed argv list; never accepts arbitrary shell fragments."""

    if target_version not in ALLOWED_VERSIONS:
        raise ValueError("target_version is not allowlisted")
    return [
        sys.executable,
        "-m",
        "podman_compose",
        "-f",
        "compose.yaml",
        "up",
        "--detach",
        "--build",
        "--force-recreate",
        "demo-service",
        "traffic-generator",
    ]


def rollback_release(target_version: Literal["v1", "v2"], timeout_seconds: int = 60) -> dict[str, object]:
    """Execute the fixed rollback command with shell disabled."""

    command = build_rollback_command(target_version)
    environment = {**os.environ, "DEMO_VERSION": target_version}
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        shell=False,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    return {
        "command": [command[0], "compose", "up", "demo-service"],
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4096:],
        "stderr": completed.stderr[-4096:],
    }
