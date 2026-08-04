"""Operational commands for deterministic experience extraction."""

import asyncio
from typing import Annotated

import typer
from sqlalchemy import select

from oncall.database import async_session, get_engine
from oncall.experience.service import (
    ExperienceNotEligible,
    create_candidate_from_resolved_incident,
)
from oncall.incidents.state import IncidentStatus
from oncall.models import Incident


app = typer.Typer(help="回填已解决 Incident 的经验候选。")


async def _backfill(limit: int) -> tuple[int, int]:
    async with async_session() as session:
        incident_ids = list(
            (
                await session.scalars(
                    select(Incident.id)
                    .where(Incident.status == IncidentStatus.RESOLVED)
                    .order_by(Incident.resolved_at.asc())
                    .limit(limit)
                )
            ).all()
        )
    created = 0
    skipped = 0
    for incident_id in incident_ids:
        async with async_session() as session:
            try:
                candidate = await create_candidate_from_resolved_incident(session, incident_id)
                await session.commit()
            except ExperienceNotEligible:
                await session.rollback()
                skipped += 1
                continue
            created += int(candidate is not None)
            skipped += int(candidate is None)
    await get_engine().dispose()
    return created, skipped


@app.command("backfill")
def backfill(
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 1000,
) -> None:
    """Create missing candidates for previously resolved, fully verified Incidents."""

    created, skipped = asyncio.run(_backfill(limit))
    typer.echo(f"经验回填完成：创建 {created} 个候选，跳过 {skipped} 个 Incident。")
