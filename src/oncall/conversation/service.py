"""Conversation persistence, grounded answers, and safe Incident commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.agents.model_factory import create_chat_model
from oncall.audit.service import append_audit_event
from oncall.conversation.intents import route_message
from oncall.conversation.memory import MemoryProvider
from oncall.conversation.schemas import RouterDecision
from oncall.incidents.state import IncidentStatus, ensure_transition
from oncall.models import ActionPlan, Conversation, ConversationMessage, Incident, User
from oncall.rag.schemas import KnowledgeQuery
from oncall.rag.service import search_knowledge


@dataclass
class TurnResult:
    user_message: ConversationMessage
    assistant_message: ConversationMessage
    route: RouterDecision
    memory_used: int
    enqueue_incident: tuple[UUID, int] | None = None


async def _knowledge_answer(
    decision: RouterDecision,
    text: str,
    memories: list[str],
    *,
    live_mode: bool,
    conversation_context: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    query = KnowledgeQuery(
        project_id=decision.project_id,
        text=(f"{conversation_context[-1600:]}\n{text}" if conversation_context else text),
        service=decision.service,
        environment=decision.environment,
        limit=5,
    )
    try:
        hits = await asyncio.to_thread(search_knowledge, query)
    except Exception:
        hits = []
    citations = [
        {
            "document_id": hit.document_id,
            "title": hit.title,
            "section": hit.section_path,
            "source_path": hit.source_path,
            "score": hit.score,
            "excerpt": hit.content[:600],
        }
        for hit in hits
    ]
    if not hits:
        return "当前已审核的运维知识库中没有找到足够依据，请补充服务名或问题现象。", []
    context = "\n\n".join(
        f"[{index + 1}] {hit.title} / {hit.section_path}\n{hit.content[:1600]}"
        for index, hit in enumerate(hits)
    )
    memory_text = "\n".join(f"- {item}" for item in memories)
    if live_mode:
        try:
            response = await create_chat_model("conversation-knowledge").ainvoke(
                [
                    SystemMessage(
                        content=(
                            "You are an OnCall knowledge assistant. Answer in Chinese using only the "
                            "approved citations. Cite sources as [1], [2]. Memories are contextual hints, "
                            "not operational truth. Never claim to have executed an operation.\n"
                            f"Long-term memory:\n{memory_text or '(none)'}\n\nCitations:\n{context}"
                            f"\n\nRecent conversation:\n{conversation_context[-3000:] or '(none)'}"
                        )
                    ),
                    HumanMessage(content=text),
                ]
            )
            answer = str(response.content).strip()
            if answer:
                return answer[:8000], citations
        except Exception:
            pass
    first = hits[0]
    return f"根据《{first.title}》的“{first.section_path}”：{first.content[:900]}", citations


async def _find_incident(session: AsyncSession, decision: RouterDecision) -> Incident | None:
    if decision.incident_id is not None:
        return await session.get(Incident, decision.incident_id)
    statement = select(Incident).order_by(Incident.opened_at.desc()).limit(1)
    if decision.service:
        statement = statement.where(Incident.service == decision.service)
    if decision.environment:
        statement = statement.where(Incident.environment == decision.environment)
    return await session.scalar(statement)


async def _incident_answer(
    session: AsyncSession,
    conversation: Conversation,
    decision: RouterDecision,
    user: User,
) -> tuple[str, dict[str, Any], tuple[UUID, int] | None]:
    if decision.action == "APPROVAL_BLOCKED":
        return (
            "对话入口不允许审批或执行恢复操作。请前往 Incident 详情页核对固定计划哈希后完成审批。",
            {"safety_boundary": "approval_ui_required"},
            None,
        )
    incident = await _find_incident(session, decision)
    if incident is None:
        return "没有找到匹配的 Incident，请提供完整 Incident ID 或服务名。", {}, None
    conversation.linked_incident_id = incident.id
    link = f"/incidents/{incident.id}"
    metadata = {"incident_id": str(incident.id), "incident_path": link}
    if decision.action == "QUERY":
        return (
            f"Incident {str(incident.id)[:8]} 当前状态为 {incident.status.value}，"
            f"服务为 {incident.service}。可打开详情页查看证据、诊断与审计时间线。",
            metadata,
            None,
        )
    plan = await session.scalar(
        select(ActionPlan)
        .where(ActionPlan.incident_id == incident.id)
        .order_by(ActionPlan.version.desc(), ActionPlan.created_at.desc())
        .limit(1)
    )
    if decision.action == "PLAN" and plan is not None:
        metadata.update({"action_plan_hash": plan.plan_hash, "plan_status": plan.status})
        return (
            f"已生成修复计划：{plan.summary} 当前状态为 {plan.status}。"
            "如需审批，请前往 Incident 详情页核对计划哈希。",
            metadata,
            None,
        )
    if user.role not in {"operator", "approver", "admin"}:
        return "当前账号只有查看权限，无法发起调查或修复规划。", metadata, None
    if incident.status in {IncidentStatus.RESOLVED, IncidentStatus.FAILED}:
        return f"该 Incident 已处于终态 {incident.status.value}，不会重新发起调查。", metadata, None
    if incident.status not in {IncidentStatus.RECEIVED, IncidentStatus.NEED_HUMAN}:
        return f"该 Incident 已处于 {incident.status.value}，无需重复发起任务。", metadata, None
    checkpoint_version = int(datetime.now(timezone.utc).timestamp())
    previous = incident.status
    if incident.status == IncidentStatus.NEED_HUMAN:
        incident.status = ensure_transition(incident.status, IncidentStatus.TRIAGING)
    await append_audit_event(
        session,
        incident.id,
        "incident.conversation_investigation_requested",
        {
            "from": previous.value,
            "checkpoint_version": checkpoint_version,
            "requested_action": decision.action,
            "conversation_id": str(conversation.id),
        },
        actor=user.username,
    )
    suffix = "并在证据充分后生成修复计划" if decision.action == "PLAN" else ""
    return (
        f"已提交 Incident {str(incident.id)[:8]} 的调查任务{suffix}。"
        "对话入口不会审批或执行任何恢复操作。",
        metadata,
        (incident.id, checkpoint_version),
    )


async def handle_turn(
    session: AsyncSession,
    conversation: Conversation,
    user: User,
    text: str,
    memory: MemoryProvider,
    *,
    live_mode: bool,
) -> TurnResult:
    recent = list(
        (
            await session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation.id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(12)
            )
        ).all()
    )
    recent.reverse()
    conversation_context = "\n".join(
        f"{item.role}: {item.content[:1000]}" for item in recent
    )
    user_message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=text.strip(),
    )
    session.add(user_message)
    decision = await route_message(
        text, live_mode=live_mode, conversation_context=conversation_context
    )
    if decision.intent == "INCIDENT" and decision.incident_id is None:
        decision.incident_id = conversation.linked_incident_id
    if decision.intent == "KNOWLEDGE":
        try:
            memories = await memory.search(text, user_id=str(user.id), limit=5)
        except Exception:
            memories = []
    else:
        memories = []
    if decision.intent == "KNOWLEDGE":
        answer, citations = await _knowledge_answer(
            decision,
            text,
            memories,
            live_mode=live_mode,
            conversation_context=conversation_context,
        )
        metadata: dict[str, Any] = {}
        enqueue = None
    else:
        answer, metadata, enqueue = await _incident_answer(
            session, conversation, decision, user
        )
        citations = []
    entities = {
        "incident_id": str(decision.incident_id) if decision.incident_id else None,
        "project_id": decision.project_id,
        "environment": decision.environment,
        "service": decision.service,
    }
    assistant_message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        intent=decision.intent,
        action=decision.action,
        entities=entities,
        citations=citations,
        metadata_=metadata,
    )
    session.add(assistant_message)
    conversation.last_intent = decision.intent
    if conversation.title == "新对话":
        conversation.title = text.strip()[:80]
    await session.flush()
    if decision.intent == "KNOWLEDGE":
        try:
            await memory.add_turn(
                user_id=str(user.id),
                conversation_id=str(conversation.id),
                user_text=text,
                assistant_text=answer,
            )
        except Exception:
            # Long-term memory is an enhancement; it cannot make the durable turn fail.
            pass
    return TurnResult(
        user_message=user_message,
        assistant_message=assistant_message,
        route=decision,
        memory_used=len(memories),
        enqueue_incident=enqueue,
    )
