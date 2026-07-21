/// <reference types="vite/client" />

/**
 * Typed browser client for the Lazarus Stage 2 HTTP API.
 *
 * This module deliberately mirrors the API's public response shapes.  It does
 * not infer pipeline state locally: callers always render the API's persisted
 * run status and receipt-derived stages.
 */

export type RunStatus = "queued" | "running" | "completed" | "halted" | "error";

export interface CreateRunRequest {
  repo_url: string;
  owner: string;
  repo: string;
  ref?: string | null;
  include_closed?: boolean;
  skip_triage?: boolean;
  health_report_only?: boolean;
}

export interface RunStage {
  stage: string;
  status: string;
}

export interface RunSummary {
  id: string;
  status: RunStatus;
  repo_url: string;
  github_owner: string;
  github_repo: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunDetail extends RunSummary {
  exit_code: number | null;
  error_message: string | null;
  stages: RunStage[];
}

export interface ArtifactList {
  run_id: string;
  artifacts: string[];
}

export interface HealthResponse {
  status: string;
}

export interface ArtifactPayload {
  blob: Blob;
  contentType: string | null;
  filename: string | null;
}

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

/** A display-safe failure decoded from the API's ``{ detail: ... }`` body. */
export class LazarusApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: unknown;

  constructor(status: number, detail: string, body: unknown = null) {
    super(detail);
    this.name = "LazarusApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();

/** The configured API root, without a trailing slash. */
export const API_BASE_URL = (configuredBaseUrl || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

function runPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}`;
}

/**
 * Artifact paths come from the API's directory listing, but they are still
 * treated as untrusted display data in the browser. In particular, URL
 * normalisation would turn literal ``..`` segments into a different route
 * before the request reached the API's containment check. Rejecting them on
 * the client keeps malformed data from becoming a download/navigation URL.
 */
export function isSafeArtifactPath(path: string): boolean {
  if (!path || path.startsWith("/") || path.includes("\\") || path.includes("\0")) {
    return false;
  }

  return path
    .split("/")
    .every((segment) => segment.length > 0 && segment !== "." && segment !== "..");
}

function artifactPath(runId: string, path: string): string | null {
  if (!isSafeArtifactPath(path)) {
    return null;
  }

  // Preserve slash separators for the FastAPI ``{artifact_path:path}`` route,
  // but encode each segment so a filename cannot change the requested route.
  const encodedSegments = path.split("/").map((segment) => encodeURIComponent(segment));
  return `${runPath(runId)}/artifacts/${encodedSegments.join("/")}`;
}

function errorDetail(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail)) {
      const validationMessages = detail
        .map((item) => {
          if (typeof item !== "object" || item === null) {
            return String(item);
          }
          const typedItem = item as { loc?: unknown; msg?: unknown };
          const location = Array.isArray(typedItem.loc)
            ? typedItem.loc.map(String).join(".")
            : "request";
          const message =
            typeof typedItem.msg === "string"
              ? typedItem.msg
              : (JSON.stringify(item) ?? "unreadable validation error");
          return `${location}: ${message}`;
        })
        .filter(Boolean);
      if (validationMessages.length > 0) {
        return validationMessages.join("; ");
      }
    }
    if (detail !== undefined && detail !== null) {
      return typeof detail === "string" ? detail : (JSON.stringify(detail) ?? fallback);
    }
  }
  return fallback;
}

async function readResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  const bodyText = await response.text();
  if (!bodyText) {
    return null;
  }
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(bodyText) as unknown;
    } catch {
      // A malformed response is still useful to surface as text below.
    }
  }
  return bodyText;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers: {
        Accept: "application/json",
        ...init.headers,
      },
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    const reason = error instanceof Error ? error.message : String(error);
    throw new LazarusApiError(
      0,
      `Could not reach the Lazarus API at ${API_BASE_URL}: ${reason}`,
    );
  }

  const body = await readResponseBody(response);
  if (!response.ok) {
    throw new LazarusApiError(
      response.status,
      errorDetail(body, `Lazarus API request failed (${response.status}).`),
      body,
    );
  }
  return body as T;
}

function filenameFromDisposition(contentDisposition: string | null): string | null {
  if (!contentDisposition) {
    return null;
  }
  const match = /filename="?([^";]+)"?/i.exec(contentDisposition);
  return match?.[1] ?? null;
}

/** GET /health. */
export function getHealth(options: ApiRequestOptions = {}): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal: options.signal });
}

/** POST /runs. The API returns a queued ``RunSummary`` with HTTP 202. */
export function createRun(requestBody: CreateRunRequest): Promise<RunSummary> {
  return request<RunSummary>("/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody),
  });
}

/** GET /runs, newest first. */
export function listRuns(
  limit = 50,
  offset = 0,
  options: ApiRequestOptions = {},
): Promise<RunSummary[]> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<RunSummary[]>(`/runs?${query.toString()}`, { signal: options.signal });
}

/** GET /runs/{run_id}. */
export function getRun(
  runId: string,
  options: ApiRequestOptions = {},
): Promise<RunDetail> {
  return request<RunDetail>(runPath(runId), { signal: options.signal });
}

/** POST /runs/{run_id}/cancel. */
export function cancelRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`${runPath(runId)}/cancel`, { method: "POST" });
}

/** GET /runs/{run_id}/artifacts. */
export function listArtifacts(
  runId: string,
  options: ApiRequestOptions = {},
): Promise<ArtifactList> {
  return request<ArtifactList>(`${runPath(runId)}/artifacts`, {
    signal: options.signal,
  });
}

/**
 * GET /runs/{run_id}/artifacts/{artifact_path} as a Blob.
 *
 * Call ``payload.blob.text()`` for an inline text viewer, or use
 * ``artifactDownloadUrl`` with an anchor for a browser-managed download.
 */
export async function getArtifact(
  runId: string,
  path: string,
  options: ApiRequestOptions = {},
): Promise<ArtifactPayload> {
  const pathToFetch = artifactPath(runId, path);
  if (!pathToFetch) {
    throw new LazarusApiError(
      400,
      "Unsafe artifact path was refused. Artifact paths must be relative and cannot contain traversal segments.",
    );
  }

  let response: Response;
  try {
    response = await fetch(apiUrl(pathToFetch), {
      signal: options.signal,
      headers: { Accept: "text/plain, text/markdown, application/json, */*" },
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    const reason = error instanceof Error ? error.message : String(error);
    throw new LazarusApiError(
      0,
      `Could not reach the Lazarus API at ${API_BASE_URL}: ${reason}`,
    );
  }

  if (!response.ok) {
    const body = await readResponseBody(response);
    throw new LazarusApiError(
      response.status,
      errorDetail(body, `Could not retrieve artifact (${response.status}).`),
      body,
    );
  }

  return {
    blob: await response.blob(),
    contentType: response.headers.get("content-type"),
    filename: filenameFromDisposition(response.headers.get("content-disposition")),
  };
}

/**
 * A safe, encoded direct URL for anchors and browser downloads, or ``null``
 * if a malformed artifact name should not be made navigable.
 */
export function artifactDownloadUrl(runId: string, path: string): string | null {
  const pathToDownload = artifactPath(runId, path);
  return pathToDownload ? apiUrl(pathToDownload) : null;
}
