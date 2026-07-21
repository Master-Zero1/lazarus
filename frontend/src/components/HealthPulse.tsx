import { StatusIndicator } from "./UiBits";

export type HealthState = "checking" | "online" | "offline";

export function HealthPulse({ state, message }: { state: HealthState; message?: string }) {
  const status = state === "online" ? "completed" : state === "offline" ? "error" : "queued";
  const label = state === "online" ? "API uplink" : state === "offline" ? "API unreachable" : "checking API";

  return (
    <div className="health-indicator" title={message} aria-live="polite">
      <StatusIndicator status={status}>{label}</StatusIndicator>
    </div>
  );
}
