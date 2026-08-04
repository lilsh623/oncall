"""Select active learned Skills first, with Git-managed Skills as a fallback."""

from __future__ import annotations

from sqlalchemy import select

from oncall.database import async_session
from oncall.graph.contracts import IncidentGraphState
from oncall.models import SkillVersion
from oncall.skills.registry import find_candidate_skills
from oncall.skills.schemas import SkillDefinition, SkillManifest


def _alert_name(summary: str) -> str:
    for name in ("HighErrorRate", "HighLatency"):
        if name.lower() in summary.lower():
            return name
    return "HighErrorRate"


async def select_investigation_skill(
    state: IncidentGraphState,
) -> SkillDefinition | None:
    async with async_session() as session:
        learned = await session.scalar(
            select(SkillVersion)
            .where(
                SkillVersion.project_id == state.project_id,
                SkillVersion.environment == state.environment,
                SkillVersion.service == state.service,
                SkillVersion.status == "ACTIVE",
            )
            .order_by(SkillVersion.published_at.desc())
            .limit(1)
        )
    if learned is not None:
        manifest = SkillManifest.model_validate(learned.manifest)
        return SkillDefinition(
            **manifest.model_dump(by_alias=True),
            project_id=learned.project_id,
            instructions=learned.instructions,
            source_path=f"database:skill_versions:{learned.id}",
        )
    candidates = find_candidate_skills(
        {
            "project_id": state.project_id,
            "alert_name": _alert_name(state.alert_summary),
            "labels": {"correlation_key": "release-regression"},
        },
        "http-api",
    )
    return candidates[0] if candidates else None
