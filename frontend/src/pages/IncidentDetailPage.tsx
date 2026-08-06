import { useCallback, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { approve, getIncident, reject, retryIncident } from "../features/incidents/api";
import { AuditPanel } from "../features/incidents/AuditPanel";
import { EvidencePanel } from "../features/incidents/EvidencePanel";
import { IncidentOverview } from "../features/incidents/IncidentOverview";
import { IncidentTimeline } from "../features/incidents/IncidentTimeline";
import { KnowledgePanel } from "../features/incidents/KnowledgePanel";
import { RemediationPanel } from "../features/incidents/RemediationPanel";
import { StatusBadge } from "../components/StatusBadge";

export function IncidentDetailPage() {
  const { incidentId = "" } = useParams(); const { client, user } = useAuth(); const cache = useQueryClient();
  const query = useQuery({ queryKey: ["incident", incidentId], queryFn: () => getIncident(client, incidentId), enabled: Boolean(incidentId), refetchInterval: 15000 });
  const refresh = useCallback(() => cache.invalidateQueries({ queryKey: ["incident", incidentId] }), [cache, incidentId]);
  const approval = useMutation({ mutationFn: (hash: string) => approve(client, incidentId, hash), onSuccess: refresh });
  const rejection = useMutation({ mutationFn: (hash: string) => reject(client, incidentId, hash), onSuccess: refresh });
  const retry = useMutation({ mutationFn: () => retryIncident(client, incidentId), onSuccess: refresh });
  useEffect(() => { let cancelled = false; let reader: ReadableStreamDefaultReader<Uint8Array> | undefined; const start = async () => { try { const response = await client.openStream(`/api/v1/incidents/${incidentId}/stream`); reader = response.body!.getReader(); const decoder = new TextDecoder(); let buffer = ""; while (!cancelled) { const item = await reader.read(); if (item.done) break; buffer += decoder.decode(item.value, { stream: true }); while (buffer.includes("\n\n")) { const boundary = buffer.indexOf("\n\n"); const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); if (block.includes("data:")) void refresh(); } } } catch { /* polling remains available when the live stream is interrupted */ } }; if (incidentId) void start(); return () => { cancelled = true; void reader?.cancel(); }; }, [client, incidentId, refresh]);
  if (query.isLoading) return <main><div className="loading-row">正在加载事件完整上下文…</div></main>;
  if (query.error || !query.data) return <main><div className="error-banner">{query.error?.message ?? "Incident 不存在"}</div></main>;
  const detail = query.data; const overview = detail.overview; const status = String(overview.status); const canApprove = user?.role === "approver" || user?.role === "admin"; const canRetry = user?.role !== "viewer" && status === "NEED_HUMAN";
  return <main><Link className="back-link" to="/incidents">← 返回事件中心</Link><div className="incident-hero"><div><div className="incident-kicker"><span>INCIDENT #{String(overview.id).slice(0, 8)}</span><StatusBadge status={status} /></div><h1>{overview.title}</h1><p>{overview.project_id} / {overview.environment} · {overview.service}</p></div><div className="hero-actions"><span className="live-indicator"><i />事件流已连接</span>{canRetry && <button disabled={retry.isPending} onClick={() => retry.mutate()}>{retry.isPending ? "正在重新投递…" : "重新发起调查"}</button>}</div></div>
    <IncidentOverview overview={overview} /><IncidentTimeline events={detail.audit_events} /><div className="detail-grid"><EvidencePanel evidence={detail.evidence} hypotheses={detail.hypotheses} /><KnowledgePanel citations={detail.knowledge_citations} /></div><RemediationPanel plans={detail.action_plans} canApprove={canApprove} onApprove={(hash) => approval.mutate(hash)} onReject={(hash) => rejection.mutate(hash)} />{(approval.error || rejection.error || retry.error) && <div className="error-banner">{String(approval.error ?? rejection.error ?? retry.error)}</div>}<AuditPanel events={detail.audit_events} /></main>;
}
