import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { getIncidents } from "../features/incidents/api";

export function IncidentListPage() { const { client } = useAuth(); const query = useQuery({ queryKey: ["incidents"], queryFn: () => getIncidents(client) }); if (query.isLoading) return <p>正在加载 Incident…</p>; if (query.error) return <p className="error">{query.error.message}</p>; return <main><h1>Incident</h1><table><thead><tr><th>ID</th><th>标题</th><th>服务</th><th>状态</th><th>严重度</th><th>开始时间</th></tr></thead><tbody>{query.data?.map((incident) => <tr key={incident.id}><td><Link to={`/incidents/${incident.id}`}>{incident.id.slice(0, 8)}</Link></td><td>{incident.title}</td><td>{incident.service}</td><td>{incident.status}</td><td>{incident.severity ?? "—"}</td><td>{new Date(incident.opened_at).toLocaleString()}</td></tr>)}</tbody></table></main>; }
