export type ConversationSummary = {
  id: string; title: string; status: string;
  last_intent: "KNOWLEDGE" | "INCIDENT" | null;
  linked_incident_id: string | null; created_at: string; updated_at: string;
};
export type ConversationMessage = {
  id: string; role: "user" | "assistant"; content: string;
  intent: string | null; action: string | null;
  entities: Record<string, unknown>; citations: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>; created_at: string;
};
export type ConversationDetail = ConversationSummary & { messages: ConversationMessage[] };
export type TurnResponse = {
  conversation: ConversationSummary; user_message: ConversationMessage;
  assistant_message: ConversationMessage;
  route: { intent: "KNOWLEDGE" | "INCIDENT"; action: string; confidence: number };
  memory_used: number; enqueued: boolean;
};
