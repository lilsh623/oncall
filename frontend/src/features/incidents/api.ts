import type { ApiClient } from "../../api/client";
import type { IncidentDetail, IncidentListItem } from "./types";

export async function getIncidents(client: ApiClient): Promise<IncidentListItem[]> { return (await client.request<{ items: IncidentListItem[] }>("/api/v1/incidents")).items; }
export function getIncident(client: ApiClient, id: string): Promise<IncidentDetail> { return client.request(`/api/v1/incidents/${id}`); }
export function approve(client: ApiClient, id: string, action_plan_hash: string): Promise<unknown> { return client.request(`/api/v1/incidents/${id}/approvals`, { method: "POST", body: JSON.stringify({ action_plan_hash }) }); }
export function reject(client: ApiClient, id: string, action_plan_hash: string): Promise<unknown> { return client.request(`/api/v1/incidents/${id}/rejections`, { method: "POST", body: JSON.stringify({ action_plan_hash }) }); }
