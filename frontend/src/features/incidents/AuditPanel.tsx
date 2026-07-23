import type { AuditEvent } from "./types";
export function AuditPanel({ events }: { events: AuditEvent[] }) { return <section><h2>审计</h2><pre>{JSON.stringify(events, null, 2)}</pre></section>; }
