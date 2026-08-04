import type { ApiClient } from "../../api/client";
import type { ConversationDetail, ConversationSummary, TurnResponse } from "./types";
export function getConversations(client: ApiClient): Promise<ConversationSummary[]> { return client.request("/api/v1/conversations"); }
export function createConversation(client: ApiClient): Promise<ConversationSummary> { return client.request("/api/v1/conversations", { method: "POST", body: JSON.stringify({ title: null }) }); }
export function getConversation(client: ApiClient, id: string): Promise<ConversationDetail> { return client.request(`/api/v1/conversations/${id}`); }
export function sendConversationMessage(client: ApiClient, id: string, content: string): Promise<TurnResponse> { return client.request(`/api/v1/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ content }) }); }
