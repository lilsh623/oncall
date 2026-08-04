"""Agent evaluation run and drill-down APIs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.auth.dependencies import CurrentUser, get_db_session, require_roles
from oncall.evaluation.runner import create_evaluation_run_record
from oncall.evaluation.schemas import (
    EvaluationCaseView,
    EvaluationMetrics,
    EvaluationRunCreate,
    EvaluationRunDetail,
    EvaluationRunView,
)
from oncall.models import EvaluationCaseResult, EvaluationRun, User
from oncall.jobs.tasks import run_agent_evaluation


router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])
Session = Annotated[AsyncSession, Depends(get_db_session)]
RunnerUser = Annotated[User, Depends(require_roles("operator", "approver", "admin"))]


def _run_view(run: EvaluationRun) -> EvaluationRunView:
    return EvaluationRunView(
        id=run.id,
        mode=run.mode,
        dataset_name=run.dataset_name,
        dataset_version=run.dataset_version,
        status=run.status,
        case_count=run.case_count,
        passed_count=run.passed_count,
        metrics=EvaluationMetrics.model_validate(run.metrics),
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=run.error,
        created_at=run.created_at,
    )


def _case_view(row: EvaluationCaseResult) -> EvaluationCaseView:
    return EvaluationCaseView.model_validate(row, from_attributes=True)


@router.post("/runs", response_model=EvaluationRunView, status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    payload: EvaluationRunCreate, session: Session, user: RunnerUser
) -> EvaluationRunView:
    run = await create_evaluation_run_record(session, mode=payload.mode, actor=user)
    await session.commit()
    await session.refresh(run)
    run_agent_evaluation.delay(str(run.id))
    return _run_view(run)


@router.get("/runs", response_model=list[EvaluationRunView])
async def list_runs(
    session: Session,
    _: CurrentUser,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[EvaluationRunView]:
    rows = list(
        (
            await session.scalars(
                select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(limit)
            )
        ).all()
    )
    return [_run_view(row) for row in rows]


@router.get("/runs/{run_id}", response_model=EvaluationRunDetail)
async def read_run(run_id: UUID, session: Session, _: CurrentUser) -> EvaluationRunDetail:
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="evaluation run not found")
    cases = list(
        (
            await session.scalars(
                select(EvaluationCaseResult)
                .where(EvaluationCaseResult.run_id == run.id)
                .order_by(EvaluationCaseResult.created_at.asc())
            )
        ).all()
    )
    return EvaluationRunDetail(**_run_view(run).model_dump(), cases=[_case_view(row) for row in cases])
