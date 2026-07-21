import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  artifactDownloadUrl,
  getArtifact,
  isSafeArtifactPath,
  type LazarusApiError,
} from "../api/client";

const TEXT_EXTENSIONS = new Set([
  "md",
  "txt",
  "json",
  "log",
  "yml",
  "yaml",
  "toml",
  "ini",
  "cfg",
]);

function isTextArtifact(path: string): boolean {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  return TEXT_EXTENSIONS.has(extension);
}

export interface ReportViewerProps {
  runId: string | null;
  artifacts: string[];
  loading: boolean;
  error: string | null;
}

export function ReportViewer({ runId, artifacts, loading, error }: ReportViewerProps) {
  const reportPath = artifacts.find((artifact) => artifact === "revival_report.md") ?? null;
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setSelectedPath((current) => {
      if (current && artifacts.includes(current) && isSafeArtifactPath(current)) return current;
      return reportPath;
    });
  }, [runId, reportPath, artifacts]);

  useEffect(() => {
    if (!runId || !selectedPath || !isTextArtifact(selectedPath)) {
      setContent(null);
      setContentError(null);
      return undefined;
    }

    const controller = new AbortController();
    setContentLoading(true);
    setContentError(null);
    getArtifact(runId, selectedPath, { signal: controller.signal })
      .then((artifact) => artifact.blob.text())
      .then((text) => {
        if (!controller.signal.aborted) setContent(text);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) {
          const apiError = cause as LazarusApiError;
          setContentError(apiError?.detail ?? "Could not load the selected artifact.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setContentLoading(false);
      });

    return () => controller.abort();
  }, [runId, selectedPath]);

  const filteredArtifacts = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return needle
      ? artifacts.filter((artifact) => artifact.toLowerCase().includes(needle))
      : artifacts;
  }, [artifacts, filter]);

  const viewerTitle = selectedPath === "revival_report.md"
    ? "Revival Report"
    : selectedPath ?? "No report yet";
  const selectedMarkdown = selectedPath?.toLowerCase().endsWith(".md");
  const initialArtifactLoading = loading && artifacts.length === 0;

  return (
    <section className="glass-panel report-panel" aria-labelledby="report-title">
      <div className="panel-header">
        <div>
          <div className="eyebrow">evidence artifacts</div>
          <h2 className="panel-title" id="report-title">{viewerTitle}</h2>
        </div>
        <span className="data-label">{artifacts.length} files observed</span>
      </div>
      {error ? <div className="diagnostic-readout report-readout">{error}</div> : null}
      {initialArtifactLoading ? <div className="loading-state">Scanning current artifact field...</div> : null}
      {!initialArtifactLoading && !runId ? (
        <div className="empty-state">
          <span className="empty-state__glyph">[ -&gt; ]</span>
          <span>Select a run to inspect its evidence.</span>
        </div>
      ) : null}
      {!initialArtifactLoading && runId && artifacts.length === 0 ? (
        <div className="empty-state">
          <span className="empty-state__glyph">[ ... ]</span>
          <span>No artifacts have arrived yet. The directory is polled while the mission runs.</span>
        </div>
      ) : null}
      {!initialArtifactLoading && runId && artifacts.length > 0 ? (
        <div className="report-layout">
          <div className="artifact-content">
            {contentLoading ? <div className="loading-state">Retrieving artifact signal...</div> : null}
            {contentError ? <div className="diagnostic-readout">{contentError}</div> : null}
            {!contentLoading && !contentError && !selectedPath ? (
              <div className="empty-state">
                <span className="empty-state__glyph">[ ? ]</span>
                <span>Choose a text artifact to inspect it inline.</span>
              </div>
            ) : null}
            {!contentLoading && !contentError && content && selectedMarkdown ? (
              <article className="markdown-view">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
              </article>
            ) : null}
            {!contentLoading && !contentError && content && !selectedMarkdown ? (
              <pre className="artifact-code"><code>{content}</code></pre>
            ) : null}
          </div>
          <details className="artifact-browser" open>
            <summary>
              <span>Artifact index</span>
              <span className="data-label">all {artifacts.length}</span>
            </summary>
            <label className="artifact-filter">
              <span className="sr-only">Filter artifacts</span>
              <input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="filter path..."
              />
            </label>
            <div className="artifact-browser__list">
              {filteredArtifacts.map((artifact) => {
                const textArtifact = isTextArtifact(artifact);
                const safeArtifact = isSafeArtifactPath(artifact);
                const downloadUrl = safeArtifact ? artifactDownloadUrl(runId, artifact) : null;
                const viewableArtifact = textArtifact && safeArtifact;

                return (
                  <div className="artifact-browser__row" key={artifact}>
                    <button
                      className={`artifact-browser__item ${selectedPath === artifact ? "artifact-browser__item--active" : ""}`}
                      type="button"
                      disabled={!viewableArtifact}
                      title={
                        !safeArtifact
                          ? "Malformed artifact path: viewing and download are blocked"
                          : textArtifact
                            ? `View ${artifact}`
                            : "Binary artifact: download only"
                      }
                      onClick={() => setSelectedPath(artifact)}
                    >
                      <span>{artifact}</span>
                    </button>
                    {downloadUrl ? (
                      <a
                        className="artifact-browser__download"
                        href={downloadUrl}
                        download
                        title={`Download ${artifact}`}
                      >
                        DL
                      </a>
                    ) : (
                      <span
                        className="artifact-browser__download artifact-browser__download--blocked"
                        title="Malformed artifact path: download blocked"
                        aria-label="Malformed artifact path blocked"
                      >
                        !
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        </div>
      ) : null}
    </section>
  );
}
