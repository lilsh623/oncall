"""OpenAI Agents SDK intelligence loop for investigation through remediation planning.

The loop owns read-only investigation, diagnosis, and plan drafting. Approval,
recovery execution, and verification intentionally remain outside this module.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Protocol

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    RunContextWrapper,
    Runner,
    function_tool,
)
from openai import AsyncOpenAI
from pydantic import Field, model_validator

from oncall.agents.knowledge import run_knowledge_agent
from oncall.agents.remediation import plan_remediation
from oncall.agents.supervisor import draft_diagnosis
from oncall.config import Settings, get_settings
from oncall.graph.contracts import (
    ActionPlanDraft,
    DiagnosisDraft,
    EvidenceItem,
    HypothesisDraft,
    IncidentGraphState,
    KnowledgeCitationDraft,
    StrictGraphModel,
)
from oncall.mcp_gateway.client import McpGateway, McpGatewayError, TOOL_REGISTRY
from oncall.rag.schemas import KnowledgeQuery
from oncall.rag.service import search_knowledge
from oncall.skills.schemas import SkillDefinition
from oncall.skills.selection import select_investigation_skill


class InvestigationAssignment(StrictGraphModel):
    objective: str = Field(max_length=2048)
    requested_checks: list[str] = Field(max_length=12)
    known_evidence_refs: list[str] = Field(max_length=30)


class DiagnosisAssignment(StrictGraphModel):
    objective: str = Field(max_length=2048)
    suspected_causes: list[str] = Field(max_length=12)
    evidence_refs: list[str] = Field(max_length=30)


class PlanningAssignment(StrictGraphModel):
    root_cause: str = Field(max_length=4096)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(max_length=30)
    requested_strategy: str = Field(max_length=2048)


class InvestigationSpecialistResult(StrictGraphModel):
    summary: str = Field(max_length=4096)
    hypotheses: list[HypothesisDraft] = Field(max_length=12)
    evidence_refs: list[str] = Field(max_length=30)
    missing_evidence: list[str] = Field(max_length=12)


class DiagnosisSpecialistResult(StrictGraphModel):
    diagnosis: DiagnosisDraft | None
    evidence_refs: list[str] = Field(max_length=30)
    additional_evidence_needed: list[str] = Field(max_length=12)
    ready_for_planning: bool

    @model_validator(mode="after")
    def require_diagnosis_when_ready(self) -> "DiagnosisSpecialistResult":
        if self.ready_for_planning and self.diagnosis is None:
            raise ValueError("ready_for_planning requires a diagnosis")
        return self


class PlanningSpecialistResult(StrictGraphModel):
    action_plan: ActionPlanDraft | None
    rationale: str = Field(max_length=4096)
    evidence_refs: list[str] = Field(max_length=30)
    blockers: list[str] = Field(max_length=12)


class IncidentIntelligenceResult(StrictGraphModel):
    status: Literal["PLAN_READY", "NEED_HUMAN"]
    diagnosis: DiagnosisDraft | None
    action_plan: ActionPlanDraft | None
    hypotheses: list[HypothesisDraft] = Field(max_length=20)
    reason: str = Field(max_length=4096)

    @model_validator(mode="after")
    def require_complete_plan(self) -> "IncidentIntelligenceResult":
        if self.status == "PLAN_READY" and (
            self.diagnosis is None or self.action_plan is None
        ):
            raise ValueError("PLAN_READY requires both diagnosis and action_plan")
        return self


@dataclass
class IncidentAgentContext:
    """Local dependencies shared by nested runs but never injected into the LLM."""

    state: IncidentGraphState
    gateway: McpGateway
    allowed_read_tools: frozenset[str]
    max_tool_calls: int
    selected_skill: SkillDefinition | None = None
    evidence: list[EvidenceItem] = field(default_factory=list)
    citations: list[KnowledgeCitationDraft] = field(default_factory=list)
    tool_call_count: int = 0


class AgentRunner(Protocol):
    async def run(
        self,
        starting_agent: Agent[IncidentAgentContext],
        input: str,
        *,
        context: IncidentAgentContext,
        max_turns: int,
        run_config: RunConfig,
    ) -> Any: ...


class SDKRunner:
    async def run(
        self,
        starting_agent: Agent[IncidentAgentContext],
        input: str,
        *,
        context: IncidentAgentContext,
        max_turns: int,
        run_config: RunConfig,
    ) -> Any:
        return await Runner.run(
            starting_agent,
            input,
            context=context,
            max_turns=max_turns,
            run_config=run_config,
        )


def _window(minutes: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(minutes=minutes), end


async def _read_mcp(
    wrapper: RunContextWrapper[IncidentAgentContext],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    context = wrapper.context
    if name not in context.allowed_read_tools:
        return {"ok": False, "code": "TOOL_NOT_ALLOWED_BY_SKILL", "tool": name}
    if context.tool_call_count >= context.max_tool_calls:
        return {"ok": False, "code": "TOOL_CALL_BUDGET_EXHAUSTED", "tool": name}
    context.tool_call_count += 1
    scope = {
        "project_id": context.state.project_id,
        "environment": context.state.environment,
        "service": context.state.service,
    }
    try:
        active_agent = getattr(wrapper, "agent", None)
        result = await context.gateway.call_read_tool(
            name,
            # Scope is authoritative application state and cannot be overridden
            # by model-generated tool arguments.
            {**arguments, **scope},
            incident_id=context.state.incident_id,
            graph_run_id=context.state.graph_run_id,
            agent_name=(
                str(getattr(active_agent, "name", "openai-agent"))[:128]
                if active_agent is not None
                else "openai-agent"
            ),
        )
    except McpGatewayError as exc:
        evidence = EvidenceItem(
            source_type="mcp",
            source_ref=f"{name}:{exc.tool_call_id}",
            observation=f"{name} failed with safe category {exc.category.value}",
            payload={"code": exc.code, "category": exc.category.value},
        )
        context.evidence.append(evidence)
        return {
            "ok": False,
            "tool": name,
            "evidence_ref": evidence.source_ref,
            "code": exc.code,
            "category": exc.category.value,
        }
    evidence = EvidenceItem(
        source_type="mcp",
        source_ref=f"{name}:{result.tool_call_id}",
        observation=f"{name} returned validated read-only data",
        payload={"truncated": result.truncated, "data": result.data},
    )
    context.evidence.append(evidence)
    return {
        "ok": True,
        "tool": name,
        "evidence_ref": evidence.source_ref,
        "truncated": result.truncated,
        "data": result.data,
    }


Minutes = Annotated[int, Field(ge=1, le=1440)]
ReadLimit = Annotated[int, Field(ge=1, le=100)]


@function_tool
async def query_service_logs(
    context: RunContextWrapper[IncidentAgentContext],
    levels: list[Literal["ERROR", "WARNING", "INFO"]],
    minutes: Minutes,
    limit: ReadLimit,
) -> dict[str, Any]:
    """Read bounded service logs; use narrower windows before broader ones."""

    start, end = _window(minutes)
    return await _read_mcp(
        context,
        "query_service_logs",
        {"start_time": start, "end_time": end, "levels": levels, "limit": limit},
    )


@function_tool
async def get_collected_evidence(
    context: RunContextWrapper[IncidentAgentContext],
) -> dict[str, Any]:
    """Return compact evidence collected by specialists in this incident run."""

    return {
        "items": [
            {
                "evidence_ref": item.source_ref,
                "observation": item.observation[:512],
                "payload": item.payload,
            }
            for item in context.context.evidence[-30:]
        ]
    }


@function_tool
async def search_approved_knowledge(
    context: RunContextWrapper[IncidentAgentContext], query: str
) -> dict[str, Any]:
    """Search only approved project-scoped SOP and runbook knowledge."""

    run_context = context.context
    if run_context.tool_call_count >= run_context.max_tool_calls:
        return {
            "ok": False,
            "code": "TOOL_CALL_BUDGET_EXHAUSTED",
            "citations": [],
        }
    run_context.tool_call_count += 1
    state = run_context.state
    try:
        hits = await asyncio.to_thread(
            search_knowledge,
            KnowledgeQuery(
                project_id=state.project_id,
                text=query[:4000],
                service=state.service,
                environment=state.environment,
                limit=5,
            ),
        )
    except Exception:
        return {"ok": False, "code": "KNOWLEDGE_UNAVAILABLE", "citations": []}
    citations = [
        KnowledgeCitationDraft(
            document_id=item.document_id,
            document_version=item.version,
            section=item.section_path[:512],
            file_path=item.source_path,
            locator=item.source_path,
            excerpt=item.content[:2048],
            score=item.score,
        )
        for item in hits[:5]
    ]
    known = {
        (item.document_id, item.locator, item.section) for item in run_context.citations
    }
    for citation in citations:
        key = (citation.document_id, citation.locator, citation.section)
        if key not in known:
            run_context.citations.append(citation)
            known.add(key)
    return {
        "ok": bool(citations),
        "citations": [item.model_dump(mode="json") for item in citations],
    }


async def _structured_output(result: Any) -> str:
    output = result.final_output
    if isinstance(output, StrictGraphModel):
        return output.model_dump_json()
    return json.dumps(output, ensure_ascii=False, default=str)


def _specialist_tools(
    specialist: Agent[IncidentAgentContext],
    *,
    tool_name: str,
    description: str,
    parameters: type[StrictGraphModel],
    max_turns: int,
) -> Any:
    return specialist.as_tool(
        tool_name=tool_name,
        tool_description=description,
        parameters=parameters,
        include_input_schema=True,
        custom_output_extractor=_structured_output,
        max_turns=max_turns,
    )


def build_incident_agents(
    model: Any,
    *,
    specialist_max_turns: int,
) -> tuple[
    Agent[IncidentAgentContext],
    Agent[IncidentAgentContext],
    Agent[IncidentAgentContext],
    Agent[IncidentAgentContext],
]:
    """Build one Supervisor and three isolated-context specialists."""

    investigator = Agent[IncidentAgentContext](
        name="Evidence Investigator",
        instructions=(
            "Role: collect validated read-only operational evidence for one incident. "
            "Use the fewest useful MCP calls, but continue when a required fact is missing. "
            "Use Tencent CLS logs as the only operational evidence source. Never invent observations, "
            "diagnose without evidence, propose writes, or request recovery tools. Return only "
            "the structured result; evidence_refs must come from tool results."
        ),
        model=model,
        model_settings=ModelSettings(temperature=0),
        tools=[
            query_service_logs,
        ],
        output_type=InvestigationSpecialistResult,
    )
    diagnostician = Agent[IncidentAgentContext](
        name="Incident Diagnostician",
        instructions=(
            "Role: determine the most supportable root cause from collected evidence. "
            "First inspect collected evidence, then retrieve approved SOP knowledge needed to "
            "support the diagnosis. Separate direct evidence from inference. If material facts "
            "are missing, set ready_for_planning=false and name the smallest additional checks. "
            "Never fabricate citations or propose execution."
        ),
        model=model,
        model_settings=ModelSettings(temperature=0),
        tools=[get_collected_evidence, search_approved_knowledge],
        output_type=DiagnosisSpecialistResult,
    )
    planner = Agent[IncidentAgentContext](
        name="Remediation Planner",
        instructions=(
            "Role: draft exactly one rollback_release plan from an evidence-backed diagnosis. "
            "Use only the incident scope and explicit release evidence. Include prerequisites, "
            "rollback target, and verification criteria. You have no recovery, shell, approval, "
            "or write tools. Return blockers instead of guessing a version or unsafe action."
        ),
        model=model,
        model_settings=ModelSettings(temperature=0),
        tools=[
            get_collected_evidence,
            search_approved_knowledge,
        ],
        output_type=PlanningSpecialistResult,
    )
    supervisor = Agent[IncidentAgentContext](
        name="Incident Supervisor",
        instructions=(
            "Role: own the intelligence loop from investigation through a proposed remediation. "
            "Delegate evidence collection, diagnosis, and planning to the three specialist tools; "
            "their model contexts are isolated, so pass a precise bounded assignment each time. "
            "After every specialist result, decide whether required evidence is sufficient. Re-run "
            "the smallest useful specialist when gaps remain. PLAN_READY requires validated MCP "
            "evidence, an approved knowledge citation, a supported diagnosis, and one scoped "
            "rollback_release plan. Stop with NEED_HUMAN when budgets are exhausted or required "
            "facts remain unavailable. Never approve, execute, verify, or request a write tool."
        ),
        model=model,
        model_settings=ModelSettings(temperature=0),
        tools=[
            _specialist_tools(
                investigator,
                tool_name="investigate_incident",
                description="Collect or extend validated read-only incident evidence.",
                parameters=InvestigationAssignment,
                max_turns=specialist_max_turns,
            ),
            _specialist_tools(
                diagnostician,
                tool_name="diagnose_incident",
                description="Diagnose collected evidence and retrieve approved SOP support.",
                parameters=DiagnosisAssignment,
                max_turns=specialist_max_turns,
            ),
            _specialist_tools(
                planner,
                tool_name="plan_remediation",
                description="Draft a non-executing rollback_release plan for approval.",
                parameters=PlanningAssignment,
                max_turns=specialist_max_turns,
            ),
        ],
        output_type=IncidentIntelligenceResult,
    )
    return supervisor, investigator, diagnostician, planner


def _model(settings: Settings) -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(
        api_key=settings.bailian_api_key.get_secret_value(),
        base_url=settings.bailian_base_url,
        timeout=30,
    )
    return OpenAIChatCompletionsModel(
        model=settings.bailian_chat_model,
        openai_client=client,
    )


def _compact_supervisor_input(state: IncidentGraphState, skill: SkillDefinition | None) -> str:
    return json.dumps(
        {
            "incident": {
                "id": state.incident_id,
                "project_id": state.project_id,
                "environment": state.environment,
                "service": state.service,
                "alert_summary": state.alert_summary,
                "created_at": state.created_at.isoformat(),
            },
            "existing_evidence": [
                {"ref": item.source_ref, "observation": item.observation[:512]}
                for item in state.evidence[-20:]
            ],
            "selected_skill": (
                {
                    "name": skill.name,
                    "version": skill.version,
                    "instructions": skill.instructions[:6000],
                    "allowed_tools": list(skill.allowed_tools),
                }
                if skill is not None
                else None
            ),
            "completion": "Return PLAN_READY only with an evidence-backed rollback plan; otherwise NEED_HUMAN.",
        },
        ensure_ascii=False,
    )


def _usable_evidence(items: list[EvidenceItem]) -> bool:
    return any(
        isinstance(item.payload, dict) and isinstance(item.payload.get("data"), dict)
        for item in items
    )


def _validate_plan_scope(
    result: IncidentIntelligenceResult,
    context: IncidentAgentContext,
) -> str | None:
    if result.status != "PLAN_READY":
        return result.reason or "The intelligence loop requested human review."
    if not _usable_evidence(context.evidence):
        return "The intelligence loop produced no validated MCP evidence."
    if not context.citations:
        return "The intelligence loop produced no approved knowledge citation."
    assert result.action_plan is not None
    rollback = result.action_plan.rollback
    state = context.state
    if (
        rollback.project_id != state.project_id
        or rollback.environment != state.environment
        or rollback.service != state.service
    ):
        return "The proposed rollback changed the incident scope."
    if rollback.current_version == rollback.target_version:
        return "The proposed rollback target equals the current release."
    return None


async def _offline_result(state: IncidentGraphState) -> dict[str, Any]:
    evidence = EvidenceItem(
        source_type="mcp",
        source_ref="tencent-cloud-contract",
        observation="Contract fixture for Tencent Cloud incident orchestration.",
        payload={"data": {"source": "cls"}},
    )
    citation = KnowledgeCitationDraft(
        document_id="tencent-cloud-runbook",
        document_version="fixture",
        section="Recovery approval",
        file_path="runbooks/tencent-cloud",
        locator="contract",
        excerpt="Approve the scoped Tencent Cloud TAT rollback plan before execution.",
        score=1.0,
    )
    prepared = state.model_copy(
        update={"evidence": [evidence], "knowledge_citations": [citation]}
    )
    diagnosis = DiagnosisDraft(
        root_cause="Tencent Cloud service regression requires an approved rollback.",
        confidence=0.7,
        summary="Contract fixture uses CLS evidence and an approved runbook citation.",
    )
    prepared = prepared.model_copy(update={"diagnosis": diagnosis})
    return {
        "status": "PLANNED",
        "evidence": [evidence.model_dump(mode="json")],
        "hypotheses": [],
        "knowledge_citations": [citation.model_dump(mode="json")],
        "selected_skill": None,
        "diagnosis": diagnosis.model_dump(mode="json"),
        "action_plan": plan_remediation(prepared).model_dump(mode="json"),
        "investigation_rounds": state.investigation_rounds + 1,
        "model_call_count": state.model_call_count,
        "no_progress_rounds": 0,
        "need_human_reason": None,
    }


async def run_incident_agent_loop(
    state: IncidentGraphState,
    *,
    live_mode: bool,
    settings: Settings | None = None,
    gateway: McpGateway | None = None,
    runner: AgentRunner | None = None,
    model: Any | None = None,
    selected_skill: SkillDefinition | None = None,
) -> dict[str, Any]:
    """Run the bounded intelligence loop and return only graph-compatible state."""

    if not live_mode:
        return await _offline_result(state)
    config = settings or get_settings()
    if selected_skill is None:
        try:
            selected_skill = await select_investigation_skill(state)
        except Exception:
            selected_skill = None
    allowed = (
        frozenset(selected_skill.allowed_tools)
        if selected_skill is not None
        else frozenset(TOOL_REGISTRY)
    )
    context = IncidentAgentContext(
        state=state,
        gateway=gateway or McpGateway(),
        allowed_read_tools=allowed,
        max_tool_calls=config.agent_max_tool_calls,
        selected_skill=selected_skill,
        evidence=list(state.evidence),
        citations=list(state.knowledge_citations),
    )
    if selected_skill is not None:
        context.evidence.append(
            EvidenceItem(
                source_type="skill",
                source_ref=f"{selected_skill.name}@{selected_skill.version}",
                observation="Selected a validated read-only investigation Skill.",
                payload={
                    "skill_name": selected_skill.name,
                    "version": selected_skill.version,
                    "source_path": selected_skill.source_path,
                },
            )
        )
    supervisor, _, _, _ = build_incident_agents(
        model or _model(config),
        specialist_max_turns=config.agent_specialist_max_turns,
    )
    run_config = RunConfig(
        tracing_disabled=not config.openai_agents_tracing_enabled,
        trace_include_sensitive_data=False,
        workflow_name="oncall-incident-intelligence",
        group_id=state.graph_run_id,
        trace_metadata={
            "incident_id": state.incident_id,
            "project_id": state.project_id,
        },
    )
    try:
        run_result = await (runner or SDKRunner()).run(
            supervisor,
            _compact_supervisor_input(state, selected_skill),
            context=context,
            max_turns=config.agent_loop_max_turns,
            run_config=run_config,
        )
        output = IncidentIntelligenceResult.model_validate(run_result.final_output)
        requests = int(getattr(run_result.context_wrapper.usage, "requests", 0))
    except Exception as exc:
        return {
            "status": "NEED_HUMAN",
            "evidence": [item.model_dump(mode="json") for item in context.evidence],
            "knowledge_citations": [
                item.model_dump(mode="json") for item in context.citations
            ],
            "investigation_rounds": min(4, state.investigation_rounds + 1),
            "model_call_count": state.model_call_count,
            "no_progress_rounds": min(2, state.no_progress_rounds + 1),
            "need_human_reason": (
                "The OpenAI Agents SDK loop failed safely: " + type(exc).__name__
            ),
        }
    invalid_reason = _validate_plan_scope(output, context)
    model_calls = min(64, state.model_call_count + requests)
    common = {
        "evidence": [item.model_dump(mode="json") for item in context.evidence],
        "hypotheses": [item.model_dump(mode="json") for item in output.hypotheses],
        "knowledge_citations": [
            item.model_dump(mode="json") for item in context.citations
        ],
        "selected_skill": (
            f"{selected_skill.name}@{selected_skill.version}"
            if selected_skill is not None
            else None
        ),
        "investigation_rounds": min(4, state.investigation_rounds + 1),
        "model_call_count": model_calls,
        "no_progress_rounds": 0 if _usable_evidence(context.evidence) else 1,
    }
    if invalid_reason is not None:
        return {
            **common,
            "status": "NEED_HUMAN",
            "need_human_reason": invalid_reason,
        }
    assert output.diagnosis is not None and output.action_plan is not None
    return {
        **common,
        "status": "PLANNED",
        "diagnosis": output.diagnosis.model_dump(mode="json"),
        "action_plan": output.action_plan.model_dump(mode="json"),
        "need_human_reason": None,
    }
