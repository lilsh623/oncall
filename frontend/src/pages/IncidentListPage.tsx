import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { getIncidents } from "../features/incidents/api";
import { StatusBadge } from "../components/StatusBadge";

export function IncidentListPage() {
  const { client } = useAuth(); const [status, setStatus] = useState(""); const [service, setService] = useState("");
  const query = useQuery({ queryKey: ["incidents", status, service], queryFn: () => getIncidents(client, { status, service }), refetchInterval: 5000 });
  return <main>
    <div className="page-heading"><div><div className="eyebrow">INCIDENT MANAGEMENT</div><h1>事件中心</h1><p>每个事件都由真实告警触发，并保留从接收到验证恢复的完整审计链路。</p></div><div className="live-indicator"><i />实时刷新</div></div>
    <section className="filter-bar"><label><span>处理状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{["RECEIVED", "TRIAGING", "INVESTIGATING", "DIAGNOSED", "PLANNING", "WAITING_APPROVAL", "EXECUTING", "VERIFYING", "RESOLVED", "NEED_HUMAN", "FAILED"].map((item) => <option key={item}>{item}</option>)}</select></label><label><span>服务</span><input placeholder="输入完整服务名" value={service} onChange={(event) => setService(event.target.value)} /></label><div className="filter-summary">当前列表 <strong>{query.data?.length ?? 0}</strong> 个事件</div></section>
    {query.error && <div className="error-banner">事件读取失败：{query.error.message}</div>}
    <section className="table-card"><div className="responsive-table"><table><thead><tr><th>事件</th><th>项目 / 环境</th><th>服务</th><th>状态</th><th>严重度</th><th>开始时间</th><th /></tr></thead><tbody>{query.data?.map((incident) => <tr key={incident.id}><td><strong>{incident.title}</strong><small className="table-subline">#{incident.id.slice(0, 8)}</small></td><td>{incident.project_id}<small className="table-subline">{incident.environment}</small></td><td>{incident.service}</td><td><StatusBadge status={incident.status} /></td><td><span className={`severity-label severity-${incident.severity ?? "unknown"}`}>{incident.severity ?? "unknown"}</span></td><td>{new Date(incident.opened_at).toLocaleString()}</td><td><Link to={`/incidents/${incident.id}`}>打开 →</Link></td></tr>)}</tbody></table></div>{query.isLoading && <div className="loading-row">正在同步 Incident…</div>}{!query.isLoading && query.data?.length === 0 && <div className="empty-state"><strong>暂无匹配事件</strong><span>新告警会由后端自动创建 Incident，无需手动录入。</span></div>}</section>
  </main>;
}
