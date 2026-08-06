import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { StatusBadge } from "../components/StatusBadge";
import { getAlerts } from "../features/alerts/api";

export function AlertInboxPage() {
  const { client } = useAuth();
  const [status, setStatus] = useState("");
  const [service, setService] = useState("");
  const [projectId, setProjectId] = useState("");
  const query = useQuery({ queryKey: ["alerts", status, service, projectId], queryFn: () => getAlerts(client, { status, service, project_id: projectId }), refetchInterval: 5000 });

  return <main>
    <div className="page-heading"><div><div className="eyebrow">ALERT HUB</div><h1>告警收件箱</h1><p>这里展示后端已鉴权、标准化并去重后的真实告警；重复投递只累计次数，不会制造事件风暴。</p></div><div className="live-indicator"><i />实时刷新</div></div>
    <section className="filter-bar"><label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option><option value="firing">告警中</option><option value="resolved">已恢复</option></select></label><label><span>项目</span><input placeholder="例如 payments" value={projectId} onChange={(event) => setProjectId(event.target.value)} /></label><label><span>服务</span><input placeholder="例如 checkout" value={service} onChange={(event) => setService(event.target.value)} /></label><div className="filter-summary">共 <strong>{query.data?.total ?? 0}</strong> 条告警</div></section>
    {query.error && <div className="error-banner">告警读取失败：{query.error.message}</div>}
    <section className="table-card"><div className="responsive-table"><table><thead><tr><th>告警</th><th>项目 / 服务</th><th>状态</th><th>级别</th><th>接收次数</th><th>最后出现</th><th>自动化事件</th></tr></thead><tbody>{query.data?.items.map((alert) => <tr key={alert.id}><td><strong>{alert.alert_name}</strong><small className="table-subline">{alert.summary ?? alert.source}</small></td><td>{alert.project_id} / {alert.service}<small className="table-subline">{alert.environment}</small></td><td><StatusBadge status={alert.status} /></td><td><span className={`severity-label severity-${alert.severity}`}>{alert.severity}</span></td><td>× {alert.occurrence_count}</td><td>{new Date(alert.last_seen).toLocaleString()}</td><td>{alert.incident_id ? <Link to={`/incidents/${alert.incident_id}`}>查看 Incident →</Link> : <span className="muted">未创建</span>}</td></tr>)}</tbody></table></div>{!query.isLoading && query.data?.items.length === 0 && <div className="empty-state"><strong>暂无匹配告警</strong><span>新告警到达后会自动出现在这里，无需手动创建 Incident。</span></div>}</section>
  </main>;
}
