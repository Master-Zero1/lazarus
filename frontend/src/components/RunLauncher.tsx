import { FormEvent, useState } from "react";

import { createRun, LazarusApiError, type CreateRunRequest, type RunSummary } from "../api/client";

export interface RunLauncherProps {
  onCreated: (run: RunSummary) => void;
}

const EMPTY_FORM: Required<Omit<CreateRunRequest, "ref">> & { ref: string } = {
  repo_url: "",
  owner: "",
  repo: "",
  ref: "",
  include_closed: false,
  skip_triage: false,
  health_report_only: false,
};

function looksLikeGitUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "https:" || parsed.protocol === "git:") && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

export function RunLauncher({ onCreated }: RunLauncherProps) {
  const [form, setForm] = useState(EMPTY_FORM);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = <Key extends keyof typeof form>(key: Key, value: (typeof form)[Key]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!looksLikeGitUrl(form.repo_url.trim())) {
      setError("Enter a public https:// or git:// repository URL. The API will perform final validation.");
      return;
    }

    setSubmitting(true);
    try {
      const run = await createRun({
        repo_url: form.repo_url.trim(),
        owner: form.owner.trim(),
        repo: form.repo.trim(),
        ref: form.ref.trim() || null,
        include_closed: form.include_closed,
        skip_triage: form.skip_triage,
        health_report_only: form.health_report_only,
      });
      onCreated(run);
    } catch (cause) {
      setError(cause instanceof LazarusApiError ? cause.detail : "The mission could not be queued.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="glass-panel" aria-labelledby="mission-launch-title">
      <div className="panel-header">
        <div>
          <div className="eyebrow">New revival</div>
          <h2 className="panel-title" id="mission-launch-title">Initiate protocol</h2>
        </div>
        <span className="data-label">Revision 01</span>
      </div>
      <div className="panel-body">
        <form className="mission-form" onSubmit={submit} noValidate>
          <div className="field mission-form__wide">
            <label htmlFor="repo-url">Repository URL</label>
            <input
              id="repo-url"
              name="repo_url"
              type="url"
              required
              placeholder="https://github.com/owner/project.git"
              value={form.repo_url}
              onChange={(event) => update("repo_url", event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="repo-owner">GitHub owner</label>
            <input
              id="repo-owner"
              name="owner"
              required
              placeholder="WuJie1010"
              value={form.owner}
              onChange={(event) => update("owner", event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="repo-name">Repository name</label>
            <input
              id="repo-name"
              name="repo"
              required
              placeholder="Facial-Expression-Recognition.Pytorch"
              value={form.repo}
              onChange={(event) => update("repo", event.target.value)}
            />
          </div>

          <button
            className="advanced-toggle mission-form__wide"
            type="button"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            {advancedOpen ? "− hide advanced parameters" : "+ advanced parameters"}
          </button>

          {advancedOpen ? (
            <div className="advanced-options">
              <div className="field">
                <label htmlFor="repo-ref">Ref (optional)</label>
                <input
                  id="repo-ref"
                  name="ref"
                  placeholder="branch, tag, or commit SHA"
                  value={form.ref}
                  onChange={(event) => update("ref", event.target.value)}
                />
              </div>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={form.include_closed}
                  onChange={(event) => update("include_closed", event.target.checked)}
                />
                Include closed issues and pull requests
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={form.skip_triage}
                  onChange={(event) => {
                    update("skip_triage", event.target.checked);
                    if (event.target.checked) update("health_report_only", false);
                  }}
                />
                Skip issue / PR triage
              </label>
              <label className="check-field">
                <input
                  type="checkbox"
                  checked={form.health_report_only}
                  onChange={(event) => {
                    update("health_report_only", event.target.checked);
                    if (event.target.checked) update("skip_triage", false);
                  }}
                />
                Stop after Health Report
              </label>
            </div>
          ) : null}

          {error ? <div className="diagnostic-readout mission-form__wide" role="alert">{error}</div> : null}
          <div className="button-row mission-form__wide">
            <button className="brutalist-button brutalist-button--primary" type="submit" disabled={submitting}>
              {submitting ? "Starting revival…" : "Start revival"}
            </button>
            <span className="form-hint">Your browser sends this request only to the configured Lazarus API.</span>
          </div>
        </form>
      </div>
    </section>
  );
}
