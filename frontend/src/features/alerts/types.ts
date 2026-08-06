export type AlertListItem = {
  id: string;
  incident_id: string | null;
  source: string;
  project_id: string;
  environment: string;
  service: string;
  alert_name: string;
  severity: string;
  status: "firing" | "resolved";
  summary: string | null;
  starts_at: string | null;
  ends_at: string | null;
  last_seen: string;
  occurrence_count: number;
};

export type AlertListResponse = {
  items: AlertListItem[];
  total: number;
  limit: number;
  offset: number;
};
