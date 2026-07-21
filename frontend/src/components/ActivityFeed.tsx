import { useEffect, useRef } from "react";

import { formatClock } from "./UiBits";

export interface ActivityEvent {
  id: string;
  at: string;
  message: string;
  emphasis?: "normal" | "success" | "warning" | "error";
}

export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    feedRef.current?.scrollTo({ top: 0, behavior: prefersReducedMotion ? "auto" : "smooth" });
  }, [events]);

  return (
    <section className="glass-panel" aria-labelledby="activity-title">
      <div className="panel-header">
        <div>
          <div className="eyebrow">derived client activity</div>
          <h2 className="panel-title" id="activity-title">Mission telemetry</h2>
        </div>
        <span className="data-label">poll diff feed</span>
      </div>
      <div className="activity-feed" ref={feedRef} aria-live="polite">
        {events.length === 0 ? (
          <div className="activity-empty">
            <span className="activity-empty__glyph" aria-hidden="true">⌁</span>
            <span>No telemetry yet. Awaiting a mission signal.</span>
          </div>
        ) : (
          events.map((event) => (
            <div className={`activity-entry activity-entry--${event.emphasis ?? "normal"}`} key={event.id}>
              <span className="activity-entry__time">{formatClock(event.at)}</span>
              <span className="activity-entry__event">{event.message}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
