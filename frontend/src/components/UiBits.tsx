import type { ReactNode } from "react";

import type { RunStatus } from "../api/client";

export type DisplayStatus = RunStatus | "preview_generated" | "receipt_pending" | "not_reached";

export function isTerminal(status: RunStatus | undefined): boolean {
  return status === "completed" || status === "halted" || status === "error";
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    completed: "completed",
    error: "error",
    halted: "halted",
    not_reached: "not reached",
    preview_generated: "draft preview",
    queued: "queued",
    receipt_pending: "receipt pending",
    running: "running",
  };
  return labels[status] ?? status.replace(/_/g, " ");
}

function dotClass(status: string): string {
  if (status === "preview_generated") {
    return "preview_generated";
  }
  if (["queued", "running", "completed", "halted", "error"].includes(status)) {
    return status;
  }
  return "queued";
}

export function StatusIndicator({
  status,
  children,
  className = "",
}: {
  status: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <span className={`status-indicator ${className}`.trim()}>
      <span className={`status-dot status-dot--${dotClass(status)}`} aria-hidden="true" />
      <span>{children ?? statusLabel(status)}</span>
    </span>
  );
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "awaiting launch";
  }
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(timestamp);
}

export function formatClock(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(timestamp);
}
