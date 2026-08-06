"""Authenticated project onboarding and integration management API."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.auth.dependencies import CurrentUser, get_db_session, require_roles
from oncall.models import AlertIntegration, CloudProject, CloudService, User
from oncall.projects.schemas import (
    IntegrationCreate,
    IntegrationCreated,
    IntegrationResponse,
    ProjectCatalogItem,
    ProjectCreate,
    ProjectResponse,
    ServiceCreate,
    ServiceResponse,
)
from oncall.projects.security import generate_integration_secret, hash_integration_secret


router = APIRouter(prefix="/api/v1/projects", tags=["projects"])
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
AdminUser = Annotated[User, Depends(require_roles("admin"))]


def _webhook_path(integration: AlertIntegration) -> str:
    return f"/api/v1/webhooks/{integration.source.replace('_', '-')}/{integration.integration_key}"


def _integration_response(integration: AlertIntegration) -> IntegrationResponse:
    return IntegrationResponse.model_validate(
        {
            **{
                key: getattr(integration, key)
                for key in IntegrationResponse.model_fields
                if key != "webhook_path"
            },
            "webhook_path": _webhook_path(integration),
        }
    )


async def _project_or_404(session: AsyncSession, project_id: UUID) -> CloudProject:
    project = await session.get(CloudProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="工程不存在")
    return project


@router.get("", response_model=list[ProjectCatalogItem])
async def list_projects(_: CurrentUser, session: DatabaseSession) -> list[ProjectCatalogItem]:
    projects = list((await session.scalars(select(CloudProject).order_by(CloudProject.name))).all())
    services = list((await session.scalars(select(CloudService))).all())
    integrations = list((await session.scalars(select(AlertIntegration))).all())
    return [
        ProjectCatalogItem(
            **ProjectResponse.model_validate(project, from_attributes=True).model_dump(),
            services=[
                ServiceResponse.model_validate(item, from_attributes=True)
                for item in services
                if item.cloud_project_id == project.id
            ],
            integrations=[
                _integration_response(item)
                for item in integrations
                if item.cloud_project_id == project.id
            ],
        )
        for project in projects
    ]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, actor: AdminUser, session: DatabaseSession
) -> CloudProject:
    project = CloudProject(**payload.model_dump(), created_by=actor.id)
    session.add(project)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="工程标识已存在")
    await session.refresh(project)
    return project


@router.post(
    "/{project_id}/services",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_service(
    project_id: UUID,
    payload: ServiceCreate,
    _: AdminUser,
    session: DatabaseSession,
) -> CloudService:
    await _project_or_404(session, project_id)
    values = payload.model_dump(mode="json")
    service = CloudService(cloud_project_id=project_id, **values)
    session.add(service)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="该环境下的服务已存在")
    await session.refresh(service)
    return service


@router.post(
    "/{project_id}/integrations",
    response_model=IntegrationCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_integration(
    project_id: UUID,
    payload: IntegrationCreate,
    _: AdminUser,
    session: DatabaseSession,
) -> IntegrationCreated:
    await _project_or_404(session, project_id)
    exists = await session.scalar(
        select(CloudService.id).where(
            CloudService.cloud_project_id == project_id,
            CloudService.environment == payload.environment,
            CloudService.service == payload.service,
            CloudService.status == "active",
        )
    )
    if exists is None:
        raise HTTPException(status_code=422, detail="请先登记该环境和服务")
    secret = generate_integration_secret()
    integration = AlertIntegration(
        cloud_project_id=project_id,
        integration_key=f"int-{uuid4().hex}",
        secret_hash=hash_integration_secret(secret),
        **payload.model_dump(mode="json"),
    )
    session.add(integration)
    await session.commit()
    await session.refresh(integration)
    return IntegrationCreated(
        **_integration_response(integration).model_dump(),
        secret=secret,
    )
