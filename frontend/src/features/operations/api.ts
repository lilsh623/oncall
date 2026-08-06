import type { ApiClient } from "../../api/client";
import type { OperationsOverview } from "./types";

export function getOperationsOverview(client: ApiClient): Promise<OperationsOverview> {
  return client.request("/api/v1/operations/overview");
}
