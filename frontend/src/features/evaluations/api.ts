import type { ApiClient } from "../../api/client";
import type { EvaluationRun, EvaluationRunDetail } from "./types";
export function getEvaluationRuns(client: ApiClient): Promise<EvaluationRun[]> { return client.request("/api/v1/evaluations/runs"); }
export function getEvaluationRun(client: ApiClient, id: string): Promise<EvaluationRunDetail> { return client.request(`/api/v1/evaluations/runs/${id}`); }
export function createEvaluationRun(client: ApiClient, mode: "offline" | "online"): Promise<EvaluationRun> { return client.request("/api/v1/evaluations/runs", { method: "POST", body: JSON.stringify({ mode }) }); }
