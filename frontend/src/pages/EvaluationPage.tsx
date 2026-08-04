import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { createEvaluationRun, getEvaluationRun, getEvaluationRuns } from "../features/evaluations/api";

const labels = [["task_success_rate", "任务成功率"], ["tool_call_success_rate", "工具调用成功率"], ["rag_hit_rate", "RAG 命中率"], ["end_to_end_resolution_rate", "端到端解决率"]] as const;
export function EvaluationPage() {
  const { client, user } = useAuth(); const queryClient = useQueryClient(); const [selectedId, setSelectedId] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["evaluation-runs"], queryFn: () => getEvaluationRuns(client) });
  useEffect(() => { if (!selectedId && runs.data?.length) setSelectedId(runs.data[0].id); }, [runs.data, selectedId]);
  const detail = useQuery({ queryKey: ["evaluation-run", selectedId], queryFn: () => getEvaluationRun(client, selectedId!), enabled: Boolean(selectedId), refetchInterval: (query) => ["PENDING", "RUNNING"].includes(query.state.data?.status ?? "") ? 2000 : false });
  const create = useMutation({ mutationFn: (mode: "offline" | "online") => createEvaluationRun(client, mode), onSuccess: async (run) => { setSelectedId(run.id); await Promise.all([queryClient.invalidateQueries({ queryKey: ["evaluation-runs"] }), queryClient.invalidateQueries({ queryKey: ["evaluation-run", run.id] })]); } });
  const canRun = user?.role !== "viewer";
  return <main><div className="page-heading"><div><h1>Agent Evaluation</h1><p>固定数据集可重复回放；在线模式会真实调用模型、RAG 与只读 MCP。</p></div><div className="actions"><button disabled={!canRun || create.isPending} onClick={() => create.mutate("offline")}>运行离线评测</button><button disabled={!canRun || create.isPending} onClick={() => create.mutate("online")}>运行在线评测</button></div></div>
    {create.isPending && <p>评测运行中，请勿重复提交…</p>}{create.error && <p className="error">{create.error.message}</p>}
    <section><h2>历史运行</h2><div className="run-tabs">{runs.data?.map((run) => <button className={run.id === selectedId ? "active" : ""} key={run.id} onClick={() => setSelectedId(run.id)}>{run.mode.toUpperCase()} · {new Date(run.created_at).toLocaleString()} · {run.passed_count}/{run.case_count}</button>)}</div></section>
    {detail.data && <><div className="metric-grid">{labels.map(([key, label]) => <section className="metric-card" key={key}><span>{label}</span><strong>{Math.round(detail.data!.metrics[key] * 100)}%</strong></section>)}</div><section><h2>样本明细</h2><table><thead><tr><th>样本</th><th>类别</th><th>结果</th><th>耗时</th><th>实际输出</th></tr></thead><tbody>{detail.data.cases.map((item) => <tr key={item.id}><td>{item.case_id}</td><td>{item.category}</td><td><span className={item.passed ? "status-pass" : "status-fail"}>{item.passed ? "PASS" : "FAIL"}</span></td><td>{item.duration_ms} ms</td><td><code>{JSON.stringify(item.actual)}</code>{item.error && <div className="error">{item.error}</div>}</td></tr>)}</tbody></table></section></>}
  </main>;
}
