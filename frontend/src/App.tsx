import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelRun,
  getHealth,
  getRun,
  LazarusApiError,
  listArtifacts,
  listRuns,
  type RunDetail,
  type RunSummary,
} from "./api/client";
import { ActivityFeed, type ActivityEvent } from "./components/ActivityFeed";
import { HealthPulse, type HealthState } from "./components/HealthPulse";
import { PipelineGraph } from "./components/PipelineGraph";
import { RecentRuns } from "./components/RecentRuns";
import { ReportViewer } from "./components/ReportViewer";
import { RunLauncher } from "./components/RunLauncher";
import { formatTimestamp, isTerminal, StatusIndicator } from "./components/UiBits";

const RUN_POLL_MS = 2_000;
const HEALTH_POLL_MS = 10_000;
const RUN_INDEX_POLL_MS = 12_000;
const ARTIFACT_POLL_MS = 8_000;

function asRunDetail(run: RunSummary): RunDetail {
  return { ...run, exit_code: null, error_message: null, stages: [] };
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof LazarusApiError ? cause.detail : fallback;
}

export default function App() {
  const [health, setHealth] = useState<HealthState>("checking");
  const [healthMessage, setHealthMessage] = useState<string | undefined>();
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<RunDetail | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [cancelling, setCancelling] = useState(false);

  const previousRunRef = useRef<RunDetail | null>(null);
  const activitySequenceRef = useRef(0);

  const addActivity = useCallback((message: string, emphasis: ActivityEvent["emphasis"] = "normal") => {
    activitySequenceRef.current += 1;
    const entry: ActivityEvent = {
      id: `${Date.now()}-${activitySequenceRef.current}`,
      at: new Date().toISOString(),
      message,
      emphasis,
    };
    setActivity((current) => [entry, ...current].slice(0, 60));
  }, []);

  const captureRunDelta = useCallback((next: RunDetail) => {
    const previous = previousRunRef.current;
    if (!previous) {
      addActivity(`linked to run ${next.id.slice(0, 8)} · ${next.status}`, next.status === "error" || next.status === "halted" ? "error" : "normal");
    } else if (previous.status !== next.status) {
      const emphasis = next.status === "completed" ? "success" : next.status === "error" || next.status === "halted" ? "error" : next.status === "running" ? "warning" : "normal";
      addActivity(`run status ${previous.status} → ${next.status}`, emphasis);
    }

    const previousStages = new Map((previous?.stages ?? []).map((stage) => [stage.stage, stage.status]));
    for (const stage of next.stages) {
      if (previousStages.get(stage.stage) !== stage.status) {
        const emphasis = stage.status === "completed" || stage.status === "preview_generated" ? "success" : "normal";
        addActivity(`stage ${stage.stage} → ${stage.status}`, emphasis);
      }
    }
    if (next.error_message && next.error_message !== previous?.error_message) {
      addActivity(`diagnostic: ${next.error_message}`, "error");
    }
    previousRunRef.current = next;
  }, [addActivity]);

  const refreshRecentRuns = useCallback(async (signal?: AbortSignal) => {
    try {
      const runs = await listRuns(50, 0, { signal });
      setRecentRuns(runs);
      setRecentError(null);
    } catch (cause) {
      if (signal?.aborted) return;
      setRecentError(errorMessage(cause, "Could not retrieve the persisted run index."));
    } finally {
      if (!signal?.aborted) setRecentLoading(false);
    }
  }, []);

  const refreshArtifacts = useCallback(async (runId: string, signal?: AbortSignal) => {
    setArtifactLoading(true);
    try {
      const result = await listArtifacts(runId, { signal });
      if (signal?.aborted) return;
      setArtifacts(result.artifacts);
      setArtifactError(null);
    } catch (cause) {
      if (signal?.aborted) return;
      setArtifactError(errorMessage(cause, "Could not retrieve run artifacts."));
    } finally {
      if (!signal?.aborted) setArtifactLoading(false);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    const refreshHealth = async () => {
      try {
        const result = await getHealth({ signal: controller.signal });
        if (disposed) return;
        setHealth(result.status === "ok" ? "online" : "offline");
        setHealthMessage(result.status === "ok" ? "Stage 2 HTTP API is reachable." : `Unexpected health response: ${result.status}`);
      } catch (cause) {
        if (disposed || controller.signal.aborted) return;
        setHealth("offline");
        setHealthMessage(errorMessage(cause, "Could not reach the Lazarus API."));
      }
    };
    void refreshHealth();
    const timer = window.setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshRecentRuns(controller.signal);
    const timer = window.setInterval(() => void refreshRecentRuns(), RUN_INDEX_POLL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refreshRecentRuns]);

  useEffect(() => {
    if (!activeRunId) return undefined;
    let disposed = false;
    let timer: number | undefined;
    const controller = new AbortController();

    const pollRun = async () => {
      try {
        const next = await getRun(activeRunId, { signal: controller.signal });
        if (disposed) return;
        setActiveRun(next);
        setRunError(null);
        captureRunDelta(next);
        void refreshRecentRuns();
        if (isTerminal(next.status)) {
          return;
        }
      } catch (cause) {
        if (!disposed && !controller.signal.aborted) {
          setRunError(errorMessage(cause, "Could not poll this run."));
          addActivity("run polling interrupted — retrying", "error");
        }
      }
      if (!disposed) timer = window.setTimeout(() => void pollRun(), RUN_POLL_MS);
    };

    void pollRun();
    return () => {
      disposed = true;
      controller.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [activeRunId, addActivity, captureRunDelta, refreshArtifacts, refreshRecentRuns]);

  useEffect(() => {
    if (!activeRunId) return undefined;
    const controller = new AbortController();
    let timer: number | undefined;
    const terminal = isTerminal(activeRun?.status);
    const pollArtifacts = async () => {
      await refreshArtifacts(activeRunId, controller.signal);
      if (!controller.signal.aborted && !terminal) {
        timer = window.setTimeout(() => void pollArtifacts(), ARTIFACT_POLL_MS);
      }
    };
    void pollArtifacts();
    return () => {
      controller.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [activeRun?.status, activeRunId, refreshArtifacts]);

  const openRun = (runId: string, knownRun?: RunSummary) => {
    previousRunRef.current = null;
    setActiveRunId(runId);
    setActiveRun(knownRun ? asRunDetail(knownRun) : null);
    setRunError(null);
    setArtifacts([]);
    setArtifactError(null);
    setActivity([]);
    addActivity(`opening persisted run ${runId.slice(0, 8)}`);
  };

  const cancelActiveRun = async () => {
    if (!activeRunId) return;
    setCancelling(true);
    setRunError(null);
    try {
      const next = await cancelRun(activeRunId);
      setActiveRun(next);
      captureRunDelta(next);
      addActivity("operator cancellation recorded", "warning");
      void refreshRecentRuns();
    } catch (cause) {
      const message = errorMessage(cause, "Cancellation could not be sent.");
      setRunError(message);
      addActivity(`cancellation response: ${message}`, "error");
    } finally {
      setCancelling(false);
    }
  };

  const activeTerminal = isTerminal(activeRun?.status);

  return (
    <div className="app-shell">
      <header className="dashboard-header">
        <div className="brand-group">
          <a className="wordmark" href="#launch" aria-label="Lazarus dashboard">LAZARUS</a>
          <p className="header-subtitle">REVIVAL OPERATIONS</p>
        </div>
        <nav className="header-nav" aria-label="Dashboard sections">
          <a href="#launch">New revival</a>
          <a href="#active-run">Active run</a>
          <a href="#artifacts">Artifacts</a>
        </nav>
        <div className="header-controls">
          <span
            className="api-demo-notice"
            title="This demo API has no authentication. Do not expose it to an untrusted network."
          >
            Demo: no auth
          </span>
          <HealthPulse state={health} message={healthMessage} />
        </div>
      </header>

      <div className="workspace">
        <aside className="operations-nav" aria-label="Operations navigation">
          <div className="operations-nav__heading">
            <span>Operations</span>
            <small>Evidence-first workflow</small>
          </div>
          <div className="operations-nav__links">
            <a className="operations-nav__link operations-nav__link--current" href="#launch">Dashboard</a>
            <a className="operations-nav__link" href="#active-run">Active run</a>
            <a className="operations-nav__link" href="#pipeline">Pipeline</a>
            <a className="operations-nav__link" href="#artifacts">Artifacts</a>
          </div>
          <a className="brutalist-button operations-nav__new" href="#launch">+ New revival</a>
          <p className="operations-nav__note">Lazarus reports evidence. It never merges or edits source code.</p>
        </aside>

        <div className="app-layout">
          <main className="app-main">
            <div id="launch" className="section-anchor">
              <RunLauncher onCreated={(run) => openRun(run.id, run)} />
            </div>

            <section className="glass-panel run-focus" id="active-run" aria-labelledby="run-focus-title">
              <div className="run-focus__header">
                <div>
                  <div className="eyebrow">Active run</div>
                  <h1 id="run-focus-title">{activeRun ? `${activeRun.github_owner}/${activeRun.github_repo}` : "No run selected"}</h1>
                  {activeRun ? <p className="terminal-text">Run #{activeRun.id.slice(0, 8)} · requested {formatTimestamp(activeRun.created_at)}</p> : null}
                </div>
                {activeRun ? (
                  <div className="run-focus__actions">
                    <StatusIndicator status={activeRun.status}>{activeRun.status}</StatusIndicator>
                    {!activeTerminal ? (
                      <button className="brutalist-button brutalist-button--danger" type="button" onClick={() => void cancelActiveRun()} disabled={cancelling}>
                        {cancelling ? "Cancelling…" : "Cancel mission"}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
              {runError ? <div className="diagnostic-readout run-focus__readout" role="alert">{runError}</div> : null}
              {activeRun?.error_message ? <div className="diagnostic-readout run-focus__readout">{activeRun.error_message}</div> : null}
            </section>

            <div id="pipeline" className="section-anchor"><PipelineGraph run={activeRun} /></div>
            <ActivityFeed events={activity} />
            <div id="artifacts" className="section-anchor"><ReportViewer runId={activeRunId} artifacts={artifacts} loading={artifactLoading} error={artifactError} /></div>
          </main>
          <RecentRuns
            runs={recentRuns}
            selectedRunId={activeRunId}
            loading={recentLoading}
            error={recentError}
            onSelect={(runId) => openRun(runId)}
          />
        </div>
      </div>
      <nav className="mobile-nav" aria-label="Mobile dashboard sections">
        <a href="#launch">Start</a>
        <a href="#active-run">Run</a>
        <a href="#pipeline">Pipeline</a>
        <a href="#artifacts">Artifacts</a>
      </nav>
    </div>
  );
}
