"""Experience review and publication API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.auth.dependencies import CurrentUser, get_db_session, require_roles
from oncall.experience.schemas import (
    ExperienceCandidateResponse,
    ExperienceResponse,
    ReviewRequest,
    SkillCandidateResponse,
    SkillVersionResponse,
)
from oncall.experience.service import publish_candidate, reject_candidate
from oncall.models import (
    Experience,
    ExperienceCandidate,
    SkillCandidate,
    SkillVersion,
    User,
)
from oncall.skills.evolution import (
    create_skill_candidate,
    publish_skill_candidate,
    reject_skill_candidate,
)


router = APIRouter(prefix="/api/v1/experiences", tags=["experiences"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
AdminUser = Annotated[User, Depends(require_roles("admin"))]


@router.get("/candidates", response_model=list[ExperienceCandidateResponse])
async def list_candidates(
    _: CurrentUser,
    session: Session,
    candidate_status: str | None = Query(default=None, alias="status", max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ExperienceCandidate]:
    statement = select(ExperienceCandidate).order_by(ExperienceCandidate.created_at.desc())
    if candidate_status is not None:
        statement = statement.where(ExperienceCandidate.status == candidate_status)
    return list((await session.scalars(statement.limit(limit))).all())


@router.get("/candidates/{candidate_id}", response_model=ExperienceCandidateResponse)
async def get_candidate(
    candidate_id: UUID, _: CurrentUser, session: Session
) -> ExperienceCandidate:
    candidate = await session.get(ExperienceCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="experience candidate not found")
    return candidate


@router.post("/candidates/{candidate_id}/publish", response_model=ExperienceResponse)
async def publish(
    candidate_id: UUID,
    payload: ReviewRequest,
    actor: AdminUser,
    session: Session,
) -> Experience:
    candidate = await session.scalar(
        select(ExperienceCandidate)
        .where(ExperienceCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="experience candidate not found")
    try:
        experience = await publish_candidate(session, candidate, actor, comment=payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    await session.commit()
    await session.refresh(experience)
    # Skill proposal generation is best-effort and happens only after the
    # reviewed Experience is durable. It can never roll back Experience publication.
    try:
        await create_skill_candidate(session, experience.id)
        await session.commit()
    except Exception:
        await session.rollback()
    return experience


@router.post("/candidates/{candidate_id}/reject", response_model=ExperienceCandidateResponse)
async def reject(
    candidate_id: UUID,
    payload: ReviewRequest,
    actor: AdminUser,
    session: Session,
) -> ExperienceCandidate:
    candidate = await session.scalar(
        select(ExperienceCandidate)
        .where(ExperienceCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="experience candidate not found")
    try:
        result = await reject_candidate(session, candidate, actor, comment=payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    await session.commit()
    await session.refresh(result)
    return result


@router.get("", response_model=list[ExperienceResponse])
async def list_published(
    _: CurrentUser,
    session: Session,
    project_id: str | None = Query(default=None, max_length=128),
    service: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Experience]:
    statement = select(Experience).where(Experience.status == "PUBLISHED")
    if project_id is not None:
        statement = statement.where(Experience.project_id == project_id)
    if service is not None:
        statement = statement.where(Experience.service == service)
    statement = statement.order_by(Experience.published_at.desc()).limit(limit)
    return list((await session.scalars(statement)).all())


@router.post(
    "/{experience_id}/skill-candidates",
    response_model=SkillCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_skill_candidate(
    experience_id: UUID, _: ReviewRequest, actor: AdminUser, session: Session
) -> SkillCandidate:
    try:
        candidate = await create_skill_candidate(session, experience_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    await session.commit()
    await session.refresh(candidate)
    return candidate


@router.get("/skill-candidates", response_model=list[SkillCandidateResponse])
async def list_skill_candidates(
    _: CurrentUser,
    session: Session,
    candidate_status: str | None = Query(default=None, alias="status", max_length=32),
) -> list[SkillCandidate]:
    statement = select(SkillCandidate).order_by(SkillCandidate.created_at.desc())
    if candidate_status is not None:
        statement = statement.where(SkillCandidate.status == candidate_status)
    return list((await session.scalars(statement.limit(100))).all())


@router.post(
    "/skill-candidates/{candidate_id}/publish",
    response_model=SkillVersionResponse,
)
async def publish_skill(
    candidate_id: UUID,
    payload: ReviewRequest,
    actor: AdminUser,
    session: Session,
) -> SkillVersion:
    candidate = await session.scalar(
        select(SkillCandidate)
        .where(SkillCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill candidate not found")
    try:
        version = await publish_skill_candidate(session, candidate, actor, comment=payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    await session.commit()
    await session.refresh(version)
    return version


@router.post(
    "/skill-candidates/{candidate_id}/reject",
    response_model=SkillCandidateResponse,
)
async def reject_skill(
    candidate_id: UUID,
    payload: ReviewRequest,
    actor: AdminUser,
    session: Session,
) -> SkillCandidate:
    candidate = await session.scalar(
        select(SkillCandidate)
        .where(SkillCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill candidate not found")
    try:
        result = await reject_skill_candidate(session, candidate, actor, comment=payload.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    await session.commit()
    await session.refresh(result)
    return result


@router.get("/skills", response_model=list[SkillVersionResponse])
async def list_learned_skills(
    _: CurrentUser,
    session: Session,
    active_only: bool = True,
) -> list[SkillVersion]:
    statement = select(SkillVersion)
    if active_only:
        statement = statement.where(SkillVersion.status == "ACTIVE")
    return list(
        (
            await session.scalars(
                statement.order_by(SkillVersion.published_at.desc()).limit(100)
            )
        ).all()
    )
