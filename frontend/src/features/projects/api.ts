import type { ApiClient } from "../../api/client";
import type { Integration, Project, Service } from "./types";
export const getProjects = (client: ApiClient) => client.request<Project[]>("/api/v1/projects");
export const createProject = (client: ApiClient, body: { project_id: string; name: string; region: string }) => client.request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify(body) });
export const createService = (client: ApiClient, projectId: string, body: Record<string, unknown>) => client.request<Service>(`/api/v1/projects/${projectId}/services`, { method: "POST", body: JSON.stringify(body) });
export const createIntegration = (client: ApiClient, projectId: string, body: Record<string, unknown>) => client.request<Integration & { secret: string }>(`/api/v1/projects/${projectId}/integrations`, { method: "POST", body: JSON.stringify(body) });
