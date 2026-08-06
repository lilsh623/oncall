import type { ApiClient } from "../../api/client";
import type { AlertListResponse } from "./types";

export function getAlerts(
  client: ApiClient,
  filters: { status?: string; service?: string; project_id?: string; limit?: string } = {},
): Promise<AlertListResponse> {
  const query = new URLSearchParams(
    Object.entries(filters).filter((entry): entry is [string, string] => Boolean(entry[1])),
  );
  return client.request(`/api/v1/alerts${query.size ? `?${query}` : ""}`);
}
