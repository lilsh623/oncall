export type Project = {
  id: string; project_id: string; name: string; provider: string; region: string; status: string;
  services: Service[]; integrations: Integration[]; created_at: string; updated_at: string;
};
export type Service = {
  id: string; cloud_project_id: string; environment: string; service: string; runtime_type: string;
  resource_config: Record<string, unknown>; observability_config: Record<string, unknown>;
  recovery_config: Record<string, unknown>; health_config: Record<string, unknown>; status: string;
  created_at: string; updated_at: string;
};
export type Integration = {
  id: string; cloud_project_id: string; integration_key: string; name: string; source: string;
  environment: string; service: string; mapping_config: Record<string, unknown>; status: string;
  last_received_at: string | null; created_at: string; updated_at: string; webhook_path: string; secret?: string;
};
