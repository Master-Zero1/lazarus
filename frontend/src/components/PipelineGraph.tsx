import { useEffect, useRef, useState } from "react";

import type { RunDetail, RunStage } from "../api/client";
import { type DisplayStatus, statusLabel, StatusIndicator } from "./UiBits";

const PIPELINE = [
  { id: "clone", short: "01", label: "clone" },
  { id: "diagnose", short: "02", label: "diagnose" },
  { id: "generate_docs", short: "03", label: "generate docs" },
  { id: "triage", short: "04", label: "triage" },
  { id: "draft_pr_preview", short: "05", label: "draft PR preview" },
  { id: "synthesize", short: "06", label: "synthesize" },
] as const;

function reportedStatus(stages: RunStage[], stageId: string): string | undefined {
  return stages.find((stage) => stage.stage === stageId)?.status;
}

function displayStatus(run: RunDetail | null, stageId: string): DisplayStatus {
  if (!run) return "not_reached";
  const reported = reportedStatus(run.stages, stageId);
  if (reported) return reported as DisplayStatus;
  if (run.status === "running") return "receipt_pending";
  if (run.status === "queued") return "queued";
  return "not_reached";
}

export function PipelineGraph({ run }: { run: RunDetail | null }) {
  const overall = run?.status ?? "queued";
  const hasReceipt = Boolean(run?.stages.length);
  const previousRunId = useRef<string | null>(null);
  const previousStatuses = useRef(new Map<string, DisplayStatus>());
  const completionTimers = useRef(new Map<string, number>());
  const [justCompleted, setJustCompleted] = useState<ReadonlySet<string>>(() => new Set());

  useEffect(() => {
    const clearTimers = () => {
      for (const timer of completionTimers.current.values()) window.clearTimeout(timer);
      completionTimers.current.clear();
    };

    if (!run) {
      clearTimers();
      previousRunId.current = null;
      previousStatuses.current = new Map();
      setJustCompleted(new Set());
      return undefined;
    }

    const currentStatuses = new Map(
      PIPELINE.map((stage) => [stage.id, displayStatus(run, stage.id)] as const),
    );

    // A persisted run may already be complete when selected.  Only animate a
    // transition observed while viewing the same run, rather than replaying
    // completion feedback for historical runs.
    if (previousRunId.current !== run.id) {
      clearTimers();
      previousRunId.current = run.id;
      previousStatuses.current = currentStatuses;
      setJustCompleted(new Set());
      return undefined;
    }

    const transitioned = new Set<string>();
    for (const [stageId, status] of currentStatuses) {
      const previous = previousStatuses.current.get(stageId);
      if (
        previous !== status &&
        (status === "completed" || status === "preview_generated")
      ) {
        transitioned.add(stageId);
      }
    }
    previousStatuses.current = currentStatuses;

    if (transitioned.size) {
      setJustCompleted((current) => new Set([...current, ...transitioned]));
      for (const stageId of transitioned) {
        const previousTimer = completionTimers.current.get(stageId);
        if (previousTimer) window.clearTimeout(previousTimer);
        completionTimers.current.set(
          stageId,
          window.setTimeout(() => {
            setJustCompleted((current) => {
              const next = new Set(current);
              next.delete(stageId);
              return next;
            });
            completionTimers.current.delete(stageId);
          }, 560),
        );
      }
    }

    return undefined;
  }, [run]);

  useEffect(() => () => {
    for (const timer of completionTimers.current.values()) window.clearTimeout(timer);
  }, []);

  return (
    <section className={`glass-panel ${overall === "running" ? "glass-panel--active" : ""}`} aria-labelledby="pipeline-title">
      <div className="panel-header">
        <div>
          <div className="eyebrow">live execution topology</div>
          <h2 className="panel-title" id="pipeline-title">Revival pipeline</h2>
        </div>
        <StatusIndicator status={overall}>{run ? `run ${overall}` : "awaiting run"}</StatusIndicator>
      </div>
      <p className="panel-copy pipeline-copy">
        {hasReceipt
          ? "Receipt received: node statuses below come directly from the completed orchestrator record."
          : run?.status === "running"
            ? "Pipeline process is active. Stage-level receipt telemetry arrives when the orchestrator writes its final receipt."
            : "Select or launch a run to illuminate the pipeline."}
      </p>
      <div className="pipeline-scroll">
        <div className={`pipeline ${overall === "running" ? "pipeline--live" : ""}`}>
          {PIPELINE.map((stage) => {
            const status = displayStatus(run, stage.id);
            const transitioned = justCompleted.has(stage.id) ? " stage-node--transitioned" : "";
            return (
              <div className={`stage-node stage-node--${status}${transitioned}`} key={stage.id}>
                <div className="stage-orb">
                  <span className="stage-orb__id">{stage.short}</span>
                </div>
                <div className="stage-name">{stage.label}</div>
                <span className="stage-status">{statusLabel(status)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
