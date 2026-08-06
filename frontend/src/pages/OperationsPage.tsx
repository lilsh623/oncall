import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { StatusBadge } from "../components/StatusBadge";
import { getAlerts } from "../features/alerts/api";
import { getIncidents } from "../features/incidents/api";
import { getOperationsOverview } from "../features/operations/api";

const stateCopy = {
  IDLE: "自动化响应链路已就绪，正在等待新的告警。",
  PROCESSING: "Agent 正在收集证据、分析根因并生成处置方案。",
  AWAITING_APPROVAL: "调查已经完成，恢复动作需要人工核对后批准。",
  ATTENTION: "部分事件已停止自动推进，需要运维人员介入。",
};

export function OperationsPage() {
  const { client } = useAuth();
  const overview = useQuery({ queryKey: ["operations-overview"], queryFn: () => getOperationsOverview(client), refetchInterval: 5000 });
  const incidents = useQuery({ queryKey: ["incidents", "overview"], queryFn: () => getIncidents(client, {}), refetchInterval: 5000 });
  const alerts = useQuery({ queryKey: ["alerts", "overview"], queryFn: () => getAlerts(client, { limit: "5" }), refetchInterval: 5000 });
  const data = overview.data;
  const copyWebhook = async (path: string) => navigator.clipboard.writeText(`${window.location.origin}${path}`);

  return <main>
    <div className="page-heading hero-heading">
      <div><div className="eyebrow">OPERATIONS CONTROL</div><h1>今天的系统，由自动化先接手</h1><p>告警进入后自动完成标准化、聚合、调查与方案生成；高风险恢复动作等待你确认。</p></div>
      {data && <div className="engine-state"><StatusBadge status={data.automation_state} /><span>{stateCopy[data.automation_state]}</span></div>}
    </div>

    {overview.error && <div className="error-banner">无法读取自动化状态：{overview.error.message}</div>}
    <div className="metric-grid overview-metrics">
      <section className="metric-card metric-alert"><span>正在告警</span><strong>{data?.firing_alerts ?? "—"}</strong><small>去重后的活跃告警</small></section>
      <section className="metric-card"><span>自动处理中</span><strong>{data?.processing_incidents ?? "—"}</strong><small>调查、规划或执行中</small></section>
      <section className="metric-card metric-warn"><span>等待你审批</span><strong>{data?.waiting_approval ?? "—"}</strong><small>恢复操作尚未执行</small></section>
      <section className="metric-card metric-success"><span>24 小时已恢复</span><strong>{data?.resolved_incidents_24h ?? "—"}</strong><small>{data?.alert_events_24h ?? 0} 次 Webhook 投递</small></section>
    </div>

    <section className="automation-card">
      <div className="section-heading"><div><span className="eyebrow">AUTOMATION PIPELINE</span><h2>告警响应链路</h2></div><small>后端实时状态 · 5 秒刷新</small></div>
      <div className="pipeline">
        <div className="pipeline-step active"><span>01</span><strong>接收告警</strong><small>鉴权 · 标准化</small></div>
        <div className="pipeline-line" />
        <div className="pipeline-step active"><span>02</span><strong>去重聚合</strong><small>关联 Incident</small></div>
        <div className="pipeline-line" />
        <div className="pipeline-step active"><span>03</span><strong>Agent 调查</strong><small>证据 · SOP · 根因</small></div>
        <div className="pipeline-line" />
        <div className="pipeline-step guarded"><span>04</span><strong>人工审批</strong><small>校验固定计划</small></div>
        <div className="pipeline-line" />
        <div className="pipeline-step"><span>05</span><strong>执行与验证</strong><small>受控恢复 · 闭环</small></div>
      </div>
    </section>

    <div className="overview-columns">
      <section>
        <div className="section-heading"><div><span className="eyebrow">RECENT INCIDENTS</span><h2>最近事件</h2></div><Link to="/incidents">查看全部 →</Link></div>
        <div className="incident-stack">
          {incidents.data?.slice(0, 4).map((incident) => <Link className="incident-row" to={`/incidents/${incident.id}`} key={incident.id}><div className={`severity-dot severity-${incident.severity ?? "unknown"}`} /><div><strong>{incident.title}</strong><small>{incident.project_id} / {incident.environment} · {incident.service}</small></div><StatusBadge status={incident.status} /><time>{new Date(incident.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></Link>)}
          {!incidents.isLoading && incidents.data?.length === 0 && <div className="empty-state compact">还没有 Incident。接入腾讯云监控或 CLS 告警后，新告警会自动出现在这里。</div>}
        </div>
      </section>
      <section>
        <div className="section-heading"><div><span className="eyebrow">ALERT INBOX</span><h2>最新告警</h2></div><Link to="/alerts">打开收件箱 →</Link></div>
        <div className="alert-stack">
          {alerts.data?.items.map((alert) => <div className="alert-row" key={alert.id}><div><strong>{alert.alert_name}</strong><small>{alert.service} · {alert.environment}</small></div><StatusBadge status={alert.status} /><time>{new Date(alert.last_seen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div>)}
          {!alerts.isLoading && alerts.data?.items.length === 0 && <div className="empty-state compact">等待第一个真实告警。</div>}
        </div>
      </section>
    </div>

    <section className="integration-card">
      <div><span className="eyebrow">TENCENT CLOUD</span><h2>腾讯云告警接入</h2><p>在“工程接入”中创建腾讯云监控或 CLS Webhook；入口密钥只展示一次，并由平台按服务范围关联告警。{data?.latest_alert_at ? ` 最近一次投递：${new Date(data.latest_alert_at).toLocaleString()}。` : " 当前尚未收到投递。"}</p></div>
      <div className="integration-list">{data?.integrations.map((integration) => <div key={integration.project_id}><span><i className="live-dot" />{integration.project_id}</span><code>{integration.webhook_path}</code><button className="secondary" onClick={() => void copyWebhook(integration.webhook_path)}>复制完整地址</button></div>)}{data && data.integrations.length === 0 && <p className="muted">请前往工程接入页创建腾讯云告警入口。</p>}</div>
    </section>
  </main>;
}
