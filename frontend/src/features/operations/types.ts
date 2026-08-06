export type OperationsOverview = {
  automation_state: "IDLE" | "PROCESSING" | "AWAITING_APPROVAL" | "ATTENTION";
  open_incidents: number;
  processing_incidents: number;
  waiting_approval: number;
  needs_human: number;
  firing_alerts: number;
  alert_events_24h: number;
  resolved_incidents_24h: number;
  latest_alert_at: string | null;
  conversation_live: boolean;
  integrations: Array<{
    source: string;
    project_id: string;
    webhook_path: string;
  }>;
};
