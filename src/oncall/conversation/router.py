"""Authenticated Conversation Router API."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.auth.dependencies import CurrentUser, get_db_session
from oncall.config import get_settings
from oncall.conversation.memory import get_memory_provider
from oncall.conversation.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    MessageCreate,
    MessageView,
    TurnResponse,
)
from oncall.conversation.service import handle_turn
from oncall.jobs.tasks import start_incident
from oncall.models import Conversation, ConversationMessage


router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])
Session = Annotated[AsyncSession, Depends(get_db_session)]


def _summary(item: Conversation) -> ConversationSummary:
    return ConversationSummary.model_validate(item, from_attributes=True)


def _message(item: ConversationMessage) -> MessageView:
    return MessageView(
        id=item.id,
        role=item.role,
        content=item.content,
        intent=item.intent,
        action=item.action,
        entities=item.entities,
        citations=item.citations,
        metadata=item.metadata_,
        created_at=item.created_at,
    )


async def _owned(session: AsyncSession, conversation_id: UUID, user_id: UUID) -> Conversation:
    item = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return item


@router.post("", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate, session: Session, user: CurrentUser
) -> ConversationSummary:
    item = Conversation(user_id=user.id, title=payload.title or "新对话")
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _summary(item)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    session: Session,
    user: CurrentUser,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[ConversationSummary]:
    items = list(
        (
            await session.scalars(
                select(Conversation)
                .where(Conversation.user_id == user.id)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return [_summary(item) for item in items]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def read_conversation(
    conversation_id: UUID, session: Session, user: CurrentUser
) -> ConversationDetail:
    item = await _owned(session, conversation_id, user.id)
    messages = list(
        (
            await session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == item.id)
                .order_by(ConversationMessage.created_at.asc())
                .limit(500)
            )
        ).all()
    )
    return ConversationDetail(**_summary(item).model_dump(), messages=[_message(row) for row in messages])


@router.post("/{conversation_id}/messages", response_model=TurnResponse)
async def send_message(
    conversation_id: UUID,
    payload: MessageCreate,
    session: Session,
    user: CurrentUser,
) -> TurnResponse:
    conversation = await _owned(session, conversation_id, user.id)
    result = await handle_turn(
        session,
        conversation,
        user,
        payload.content,
        get_memory_provider(),
        live_mode=get_settings().conversation_router_live,
    )
    await session.commit()
    if result.enqueue_incident is not None:
        incident_id, checkpoint_version = result.enqueue_incident
        start_incident.delay(str(incident_id), checkpoint_version)
    await session.refresh(conversation)
    return TurnResponse(
        conversation=_summary(conversation),
        user_message=_message(result.user_message),
        assistant_message=_message(result.assistant_message),
        route=result.route,
        memory_used=result.memory_used,
        enqueued=result.enqueue_incident is not None,
    )
