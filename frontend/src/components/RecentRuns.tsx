import type { RunSummary } from "../api/client";
import { formatTimestamp, StatusIndicator } from "./UiBits";

export interface RecentRunsProps {
  runs: RunSummary[];
  selectedRunId: string | null;
  loading: boolean;
  error: string | null;
  onSelect: (runId: string) => void;
}

export function RecentRuns({ runs, selectedRunId, loading, error, onSelect }: RecentRunsProps) {
  return (
    <aside className="glass-panel recent-runs-panel" aria-labelledby="recent-runs-title">
      <div className="panel-header">
        <div>
          <div className="eyebrow">persistent operations</div>
          <h2 className="panel-title" id="recent-runs-title">Recent runs</h2>
        </div>
        <span className="data-label">GET /runs</span>
      </div>
      {loading ? <div className="loading-state">Loading run index…</div> : null}
      {!loading && error ? <div className="diagnostic-readout sidebar-readout">{error}</div> : null}
      {!loading && !error && runs.length === 0 ? (
        <div className="empty-state">
          <span className="empty-state__glyph">[ ∅ ]</span>
          <span>No persisted runs yet.</span>
        </div>
      ) : null}
      {!loading && !error && runs.length > 0 ? (
        <div className="run-list">
          {runs.map((run) => (
            <button
              className={`run-list-item ${run.id === selectedRunId ? "run-list-item--selected" : ""}`}
              key={run.id}
              type="button"
              onClick={() => onSelect(run.id)}
              title={`Open ${run.github_owner}/${run.github_repo}`}
            >
              <StatusIndicator status={run.status} />
              <span>
                <span className="run-list-item__repo">{run.github_owner}/{run.github_repo}</span>
                <span className="run-list-item__date">{formatTimestamp(run.created_at)}</span>
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </aside>
  );
}
