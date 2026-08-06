"""Startup-loaded registry and deterministic Skill candidate selection."""

from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from oncall.skills.schemas import SkillDefinition, SkillManifest


class SkillRegistryError(RuntimeError):
    """Raised when a checked-in Skill package is invalid or ambiguous."""


class SkillRegistry:
    """Load Git-managed Skills and prefilter candidates without an LLM."""

    def __init__(self, project_packs_root: Path | None = None) -> None:
        configured_root = os.getenv("ONCALL_PROJECT_PACKS_ROOT")
        self._root = project_packs_root or (
            Path(configured_root)
            if configured_root
            else Path(__file__).resolve().parents[3] / "project-packs"
        )
        self._skills: tuple[SkillDefinition, ...] = ()

    @property
    def skills(self) -> tuple[SkillDefinition, ...]:
        return self._skills

    def load(self) -> tuple[SkillDefinition, ...]:
        """Validate every manifest and atomically publish the loaded registry."""

        if not self._root.is_dir():
            self._skills = ()
            return self._skills
        manifest_paths = sorted(self._root.glob("*/skills/*/skill.yaml"))
        if not manifest_paths:
            self._skills = ()
            return self._skills

        loaded: list[SkillDefinition] = []
        identities: set[tuple[str, str]] = set()
        for manifest_path in manifest_paths:
            project_id = manifest_path.parents[2].name
            instructions_path = manifest_path.with_name("instructions.md")
            if not instructions_path.is_file():
                raise SkillRegistryError(f"missing instructions.md for {manifest_path}")
            try:
                raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifest = SkillManifest.model_validate(raw)
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                raise SkillRegistryError(f"invalid Skill manifest {manifest_path}: {exc}") from exc

            identity = (project_id, manifest.name)
            if identity in identities:
                raise SkillRegistryError(
                    f"duplicate Skill name {manifest.name!r} for project {project_id!r}"
                )
            identities.add(identity)
            try:
                instructions = instructions_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise SkillRegistryError(
                    f"cannot read Skill instructions {instructions_path}: {exc}"
                ) from exc
            loaded.append(
                SkillDefinition(
                    **manifest.model_dump(by_alias=True),
                    project_id=project_id,
                    instructions=instructions,
                    source_path=str(manifest_path),
                )
            )

        self._skills = tuple(loaded)
        return self._skills

    def find_candidate_skills(
        self,
        alert: Mapping[str, Any] | Any,
        service_type: str,
    ) -> list[SkillDefinition]:
        """Return at most three matching Skills using stable, deterministic ranking."""

        value = alert if isinstance(alert, Mapping) else vars(alert)
        project_id = str(value.get("project_id", ""))
        alert_name = str(value.get("name") or value.get("alert_name") or "")
        labels = value.get("labels") or {}
        if not isinstance(labels, Mapping):
            return []

        ranked: list[tuple[int, str, SkillDefinition]] = []
        for skill in self._skills:
            trigger = skill.triggers
            if skill.project_id != project_id:
                continue
            matching_names = [
                pattern for pattern in trigger.alert_names if fnmatchcase(alert_name, pattern)
            ]
            if not matching_names or service_type not in trigger.service_types:
                continue
            if any(str(labels.get(key, "")) != expected for key, expected in trigger.labels.items()):
                continue
            exact_name_bonus = 20 if alert_name in matching_names else 0
            score = 100 + exact_name_bonus + 10 * len(trigger.labels)
            ranked.append((-score, skill.name, skill))

        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[:3]]


@lru_cache
def get_skill_registry() -> SkillRegistry:
    """Return the process registry; the API lifespan calls ``load`` at startup."""

    return SkillRegistry()


def find_candidate_skills(
    alert: Mapping[str, Any] | Any,
    service_type: str,
) -> list[SkillDefinition]:
    """Convenience interface required by the investigation Supervisor."""

    registry = get_skill_registry()
    if not registry.skills:
        registry.load()
    return registry.find_candidate_skills(alert, service_type)
