"""Deterministic Experience-to-Skill candidate generation and publication."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.alerts.correlation import advisory_lock_key
from oncall.audit.service import append_audit_event
from oncall.experience.extraction import canonical_hash
from oncall.models import (
    Alert,
    Experience,
    IncidentAlert,
    SkillCandidate,
    SkillVersion,
    User,
)
from oncall.skills.schemas import SkillManifest


def learned_skill_name(service: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", service.lower()).strip("_")
    normalized = normalized[:44] or "service"
    return f"learned_{normalized}_recovery"


def next_patch_version(version: str | None) -> str:
    if not version:
        return "1.0.0"
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def _output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "observations": {
                "type": "array",
                "description": "Bounded observations backed by read-only tool results.",
                "items": {"type": "string"},
            },
            "release_correlation": {
                "type": "string",
                "description": "Whether current evidence matches the reviewed experience.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence from zero to one based only on current evidence.",
            },
            "missing_evidence": {
                "type": "array",
                "description": "Facts still required before diagnosis or planning.",
                "items": {"type": "string"},
            },
        },
        "required": [
            "observations",
            "release_correlation",
            "confidence",
            "missing_evidence",
        ],
        "additionalProperties": False,
    }


def build_skill_material(
    experience: Experience,
    *,
    alert_names: list[str],
    version: str,
) -> tuple[dict[str, Any], str, str]:
    """Build a strictly read-only Skill and validate it with the runtime schema."""

    content = experience.content
    root_cause = str(content.get("root_cause") or "reviewed incident pattern")
    symptoms = [str(item) for item in content.get("symptoms", [])[:10]]
    action = content.get("action") if isinstance(content.get("action"), dict) else {}
    manifest = {
        "name": learned_skill_name(experience.service),
        "version": version,
        "description": (
            f"Investigate {experience.service} incidents that resemble reviewed "
            "recovery experience; this Skill never authorizes remediation."
        ),
        "triggers": {
            "alert_names": sorted(set(alert_names)) or ["HighErrorRate"],
            "service_types": ["http-api"],
            "labels": {},
        },
        "allowed_tools": [
            "query_metrics",
            "query_service_logs",
            "get_service_health",
            "get_active_alerts",
            "get_current_release",
            "get_recent_releases",
            "get_release_diff",
        ],
        "forbidden_actions": ["rollback", "restart", "scale", "arbitrary_write"],
        "output_schema": _output_schema(),
    }
    validated = SkillManifest.model_validate(manifest).model_dump(mode="json", by_alias=True)
    symptom_lines = "\n".join(f"- {item}" for item in symptoms) or "- No reusable symptom text."
    instructions = (
        f"# Learned investigation Skill for {experience.service}\n\n"
        "This Skill was derived from a reviewed, successfully verified Incident. "
        "Treat it as a historical investigation pattern, not as proof that the same "
        "root cause applies now.\n\n"
        f"## Reviewed pattern\n\nRoot cause: {root_cause}\n\nSymptoms:\n{symptom_lines}\n\n"
        "## Investigation procedure\n\n"
        "1. Query the current release and recent releases; compare their timestamps with the alert.\n"
        "2. Query bounded error-rate metrics, service health, active alerts, and error logs.\n"
        "3. Compare current observations with the reviewed pattern and list opposing evidence.\n"
        "4. If evidence is missing or conflicts, lower confidence and request human review.\n"
        "5. Return only structured observations; never execute or authorize remediation.\n\n"
        "## Historical action context\n\n"
        f"The source Incident used this verified action as context: {action}. "
        "Do not repeat it directly. Any new action must be created by the Remediation "
        "Planner and pass plan-hash approval, Policy Engine, and Recovery Executor controls.\n"
    )
    material_hash = canonical_hash(
        {"manifest": validated, "instructions": instructions, "experience_id": str(experience.id)}
    )
    return validated, instructions, material_hash


async def create_skill_candidate(
    session: AsyncSession, experience_id: UUID
) -> SkillCandidate:
    experience = await session.scalar(
        select(Experience).where(
            Experience.id == experience_id, Experience.status == "PUBLISHED"
        ).with_for_update()
    )
    if experience is None:
        raise ValueError("only a published experience can generate a Skill candidate")
    existing = await session.scalar(
        select(SkillCandidate).where(SkillCandidate.experience_id == experience.id)
    )
    if existing is not None:
        return existing
    skill_name = learned_skill_name(experience.service)
    latest_version = await session.scalar(
        select(SkillVersion.version)
        .where(
            SkillVersion.project_id == experience.project_id,
            SkillVersion.skill_name == skill_name,
        )
        .order_by(SkillVersion.published_at.desc())
        .limit(1)
    )
    incident_ids = [UUID(str(item)) for item in experience.source_incident_ids[:20]]
    alert_names: list[str] = []
    if incident_ids:
        alert_names = list(
            (
                await session.scalars(
                    select(Alert.alert_name)
                    .join(IncidentAlert, IncidentAlert.alert_id == Alert.id)
                    .where(IncidentAlert.incident_id.in_(incident_ids))
                )
            ).all()
        )
    version = next_patch_version(latest_version)
    manifest, instructions, content_hash = build_skill_material(
        experience, alert_names=alert_names, version=version
    )
    candidate = SkillCandidate(
        experience_id=experience.id,
        project_id=experience.project_id,
        environment=experience.environment,
        service=experience.service,
        skill_name=skill_name,
        proposed_version=version,
        manifest=manifest,
        instructions=instructions,
        content_hash=content_hash,
        source_experience_ids=[str(experience.id)],
        status="PENDING_REVIEW",
    )
    session.add(candidate)
    await session.flush()
    await append_audit_event(
        session,
        None,
        "skill.candidate_created",
        {
            "candidate_id": str(candidate.id),
            "experience_id": str(experience.id),
            "skill_name": skill_name,
            "proposed_version": version,
        },
        actor="system:skill_evolution",
    )
    return candidate


async def publish_skill_candidate(
    session: AsyncSession,
    candidate: SkillCandidate,
    actor: User,
    *,
    comment: str | None,
) -> SkillVersion:
    existing = await session.scalar(
        select(SkillVersion).where(SkillVersion.candidate_id == candidate.id)
    )
    if existing is not None:
        return existing
    if candidate.status != "PENDING_REVIEW":
        raise ValueError("Skill candidate is no longer pending review")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {
            "key": advisory_lock_key(
                "skill-version", candidate.project_id, candidate.skill_name
            )
        },
    )
    latest = await session.scalar(
        select(SkillVersion.version)
        .where(
            SkillVersion.project_id == candidate.project_id,
            SkillVersion.skill_name == candidate.skill_name,
        )
        .order_by(SkillVersion.published_at.desc())
        .limit(1)
    )
    version = next_patch_version(latest)
    manifest = {**candidate.manifest, "version": version}
    validated = SkillManifest.model_validate(manifest).model_dump(mode="json", by_alias=True)
    content_hash = canonical_hash(
        {
            "manifest": validated,
            "instructions": candidate.instructions,
            "source_experience_ids": candidate.source_experience_ids,
        }
    )
    await session.execute(
        update(SkillVersion)
        .where(
            SkillVersion.project_id == candidate.project_id,
            SkillVersion.skill_name == candidate.skill_name,
            SkillVersion.status == "ACTIVE",
        )
        .values(status="RETIRED")
    )
    candidate.proposed_version = version
    candidate.manifest = validated
    candidate.content_hash = content_hash
    candidate.status = "PUBLISHED"
    candidate.reviewed_by = actor.id
    candidate.review_comment = comment
    candidate.reviewed_at = datetime.now(UTC)
    skill_version = SkillVersion(
        candidate_id=candidate.id,
        project_id=candidate.project_id,
        environment=candidate.environment,
        service=candidate.service,
        skill_name=candidate.skill_name,
        version=version,
        manifest=validated,
        instructions=candidate.instructions,
        content_hash=content_hash,
        source_experience_ids=candidate.source_experience_ids,
        status="ACTIVE",
        published_by=actor.id,
        published_at=datetime.now(UTC),
    )
    session.add(skill_version)
    await session.flush()
    await append_audit_event(
        session,
        None,
        "skill.published",
        {
            "skill_version_id": str(skill_version.id),
            "candidate_id": str(candidate.id),
            "skill_name": candidate.skill_name,
            "version": version,
            "content_hash": content_hash,
        },
        actor=actor.username,
    )
    return skill_version


async def reject_skill_candidate(
    session: AsyncSession,
    candidate: SkillCandidate,
    actor: User,
    *,
    comment: str | None,
) -> SkillCandidate:
    if candidate.status != "PENDING_REVIEW":
        raise ValueError("Skill candidate is no longer pending review")
    candidate.status = "REJECTED"
    candidate.reviewed_by = actor.id
    candidate.review_comment = comment
    candidate.reviewed_at = datetime.now(UTC)
    await append_audit_event(
        session,
        None,
        "skill.rejected",
        {"candidate_id": str(candidate.id), "skill_name": candidate.skill_name},
        actor=actor.username,
    )
    return candidate
