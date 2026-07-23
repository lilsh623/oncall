import type { AuditEvent } from "./types";
export function IncidentTimeline({ events }: { events: AuditEvent[] }) { return <section><h2>时间线</h2><ol className="timeline">{events.map((event) => <li key={event.id}><time>{new Date(event.created_at).toLocaleString()}</time><strong>{event.event_type}</strong><span>{event.actor}</span></li>)}</ol></section>; }
