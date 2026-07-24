"""Knowledge Agent constrained to approved RAG citations."""

from __future__ import annotations

from typing import Any

from oncall.graph.contracts import IncidentGraphState, KnowledgeCitationDraft
from oncall.rag.schemas import KnowledgeQuery
from oncall.rag.service import search_knowledge


def run_knowledge_agent(
    state: IncidentGraphState,
    *,
    allow_offline_fallback: bool = False,
) -> dict[str, Any]:
    """Retrieve approved runbook/SOP citations without tool write access."""

    query_text = f"{state.alert_summary} post deployment regression rollback SOP"
    try:
        citations = search_knowledge(
            KnowledgeQuery(project_id=state.project_id, text=query_text, limit=5)
        )
        converted = [
            KnowledgeCitationDraft(
                document_id=item.document_id,
                document_version=item.version,
                section=item.section_path[:512],
                file_path=item.source_path,
                excerpt=item.content[:2048],
                score=item.score,
            )
            for item in citations[:5]
        ]
    except Exception:
        converted = []
    if not converted and allow_offline_fallback:
        converted.append(
            KnowledgeCitationDraft(
                document_id="demo-shop-post-deployment-regression-sop",
                document_version="offline-fallback",
                section="Rollback criteria",
                file_path="project-packs/demo-shop/knowledge/post-deployment-regression.md",
                locator="offline-demo",
                excerpt="When HighErrorRate follows a release and health checks fail, prepare a rollback_release plan after human approval.",
                score=0.0,
            )
        )
    return {"knowledge_citations": converted, "model_call_count": 0}
