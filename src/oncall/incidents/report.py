"""Markdown Incident report generation from persisted, public-safe facts."""

from __future__ import annotations

from oncall.incidents.schemas import IncidentDetail


def render_incident_report(detail: IncidentDetail) -> str:
    overview = detail.overview
    lines = [
        f"# Incident {overview['id']}",
        "",
        "## 摘要",
        f"- 标题：{overview['title']}",
        f"- 状态：{overview['status']}",
        f"- 范围：{overview['project_id']} / {overview['environment']} / {overview['service']}",
        f"- 开始时间：{overview['opened_at']}",
        f"- 摘要：{overview.get('summary') or '无'}",
        "",
        "## 时间线",
    ]
    if detail.audit_events:
        lines.extend(
            f"- {event.created_at} · {event.event_type} · {event.actor}"
            for event in detail.audit_events
        )
    else:
        lines.append("- 无持久化事件")
    lines.extend(["", "## 告警"])
    if detail.alerts:
        lines.extend(
            f"- [{alert['severity']}] {alert['alert_name']}：{alert.get('summary') or '无摘要'}"
            for alert in detail.alerts
        )
    else:
        lines.append("- 无")
    lines.extend(["", "## 根因与证据"])
    if detail.hypotheses:
        lines.extend(
            f"- 假设（{hypothesis.get('confidence') or 0:.2f}）：{hypothesis['description']}"
            for hypothesis in detail.hypotheses
        )
    else:
        lines.append("- 尚无确认根因")
    lines.extend(f"- 证据：{item['observation']}" for item in detail.evidence)
    lines.extend(["", "## SOP 引用"])
    if detail.knowledge_citations:
        lines.extend(
            f"- {citation['document_id']} {citation.get('section') or ''}：{citation.get('excerpt') or ''}"
            for citation in detail.knowledge_citations
        )
    else:
        lines.append("- 无")
    lines.extend(["", "## 修复计划、审批、执行与验证"])
    if detail.action_plans:
        lines.extend(
            f"- 计划 v{plan['version']}（{plan['status']}）：{plan['summary']}；哈希 {plan['plan_hash']}"
            for plan in detail.action_plans
        )
    else:
        lines.append("- 无")
    lines.extend(
        f"- 审批：{approval['decision']}，时间 {approval['decided_at']}"
        for approval in detail.approvals
    )
    lines.extend(
        f"- 执行：{execution['tool_name']} → {execution['status']}"
        for execution in detail.executions
    )
    lines.extend(
        f"- 验证：{check['name']} → {check['status']}"
        for check in detail.verification
    )
    return "\n".join(lines) + "\n"
