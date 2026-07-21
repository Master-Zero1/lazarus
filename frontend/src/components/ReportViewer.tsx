import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  artifactDownloadUrl,
  getArtifact,
  isSafeArtifactPath,
  type LazarusApiError,
} from "../api/client";
import { downloadEvidencePdf, type EvidencePdfSection } from "../pdf/evidencePacket";

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

const PDF_EVIDENCE_SOURCES: Array<{
  path: string;
  title: string;
  format: EvidencePdfSection["format"];
}> = [
  { path: "revival_report.md", title: "Revival Report", format: "markdown" },
  { path: "health_report.md", title: "Health Report", format: "markdown" },
  { path: "triage_report.md", title: "Backlog Triage Report", format: "markdown" },
  { path: "diagnosis_findings/manifest_inventory.json", title: "Manifest Inventory", format: "json" },
  { path: "diagnosis_findings/dependency_freshness.json", title: "Dependency Freshness", format: "json" },
  { path: "diagnosis_findings/ci_inventory.json", title: "CI Inventory", format: "json" },
  { path: "diagnosis_findings/test_structure_inventory.json", title: "Static Test Inventory", format: "json" },
  { path: "docs_draft/README.md", title: "Regenerated README", format: "markdown" },
  { path: "docs_draft/ARCHITECTURE.md", title: "Architecture Notes", format: "markdown" },
  { path: "docs_draft/CONTRIBUTING.md", title: "Contributing Guide", format: "markdown" },
  { path: "docs_draft/documentation_evidence.md", title: "Documentation Evidence", format: "markdown" },
  { path: "docs_draft/code_structure_inventory.json", title: "Code Structure Inventory", format: "json" },
  { path: "clone_receipt.json", title: "Clone Receipt", format: "json" },
  { path: "draft_pr_preview.json", title: "Draft PR Preview", format: "json" },
  { path: "run_receipt.json", title: "Pipeline Run Receipt", format: "json" },
];

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
  const [pdfBuilding, setPdfBuilding] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [pdfNotice, setPdfNotice] = useState<string | null>(null);

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
  const pdfSources = useMemo(
    () => PDF_EVIDENCE_SOURCES.filter((source) => artifacts.includes(source.path) && isSafeArtifactPath(source.path)),
    [artifacts],
  );

  const buildEvidencePdf = async () => {
    if (!runId || !pdfSources.length || pdfBuilding) return;
    setPdfBuilding(true);
    setPdfError(null);
    setPdfNotice(null);
    try {
      const results = await Promise.allSettled(
        pdfSources.map(async (source) => ({
          ...source,
          content: await (await getArtifact(runId, source.path)).blob.text(),
        })),
      );
      const sections: EvidencePdfSection[] = [];
      let unavailable = 0;
      for (const result of results) {
        if (result.status === "fulfilled") sections.push(result.value);
        else unavailable += 1;
      }
      if (!sections.length) {
        throw new Error("None of the selected evidence artifacts could be read for PDF export.");
      }
      await downloadEvidencePdf({ runId, sections });
      setPdfNotice(
        unavailable
          ? `Downloaded with ${unavailable} unavailable artifact${unavailable === 1 ? "" : "s"} omitted.`
          : `Downloaded ${sections.length} evidence artifact${sections.length === 1 ? "" : "s"} as PDF.`,
      );
    } catch (cause: unknown) {
      const apiError = cause as LazarusApiError;
      setPdfError(apiError?.detail ?? (cause instanceof Error ? cause.message : "Could not generate the evidence PDF."));
    } finally {
      setPdfBuilding(false);
    }
  };

  return (
    <section className="glass-panel report-panel" aria-labelledby="report-title">
      <div className="panel-header">
        <div>
          <div className="eyebrow">evidence artifacts</div>
          <h2 className="panel-title" id="report-title">{viewerTitle}</h2>
        </div>
        <div className="panel-header__actions">
          <button
            className="brutalist-button report-export-button"
            type="button"
            disabled={!runId || !pdfSources.length || pdfBuilding}
            onClick={() => void buildEvidencePdf()}
            title="Download the available reports, documentation, preview, and receipt as one PDF"
          >
            {pdfBuilding ? "Building PDF..." : "Download evidence PDF"}
          </button>
          <span className="data-label">{artifacts.length} generated</span>
        </div>
      </div>
      {error ? <div className="diagnostic-readout report-readout">{error}</div> : null}
      {pdfError ? <div className="diagnostic-readout report-readout">PDF export: {pdfError}</div> : null}
      {pdfNotice ? <div className="report-export-notice">{pdfNotice}</div> : null}
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
