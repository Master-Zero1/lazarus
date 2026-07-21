"""FastAPI transport wrapper for separate-process Lazarus pipeline runs.

Known demo limitation: this API deliberately has no authentication in this
pass.  Do not expose it publicly without adding authentication and tightening
the permissive development CORS policy below.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from lazarus.agents.docs_agent import _is_within
from lazarus.execution.clone_repo import CloneError, _validate_repository_url

from . import runner, store
from .models import ArtifactList, CreateRunRequest, HealthResponse, RunDetail, RunStage, RunSummary


_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_TEXT_MEDIA_TYPES = {
    ".log": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

app = FastAPI(title="Lazarus API", version="0.1.0")

# Deliberately permissive for the local hackathon frontend. Tighten this before
# any public deployment, together with the deliberately absent authentication.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    """Start receipt polling and resolve any stale persisted run records."""

    runner.start_poller()


@app.on_event("shutdown")
def _shutdown() -> None:
    """Stop only the observer; independent Lazarus CLI runs remain independent."""

    runner.stop_poller()


def _summary(row: dict[str, Any]) -> RunSummary:
    """Project one stored row onto the public, stable summary shape."""

    return RunSummary(
        id=row["id"],
        status=row["status"],
        repo_url=row["repo_url"],
        github_owner=row["github_owner"],
        github_repo=row["github_repo"],
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


def _receipt_stages(raw_receipt: str | None) -> list[RunStage]:
    """Surface only the existing receipt's stage/status entries without inventing state."""

    if not raw_receipt:
        return []
    try:
        receipt = json.loads(raw_receipt)
    except json.JSONDecodeError:
        return []
    stages = receipt.get("stages") if isinstance(receipt, dict) else None
    if not isinstance(stages, list):
        return []
    result: list[RunStage] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        name = stage.get("stage")
        status = stage.get("status")
        if isinstance(name, str) and isinstance(status, str):
            result.append(RunStage(stage=name, status=status))
    return result


def _detail(row: dict[str, Any]) -> RunDetail:
    """Add terminal evidence cached by the receipt watcher to one run summary."""

    return RunDetail(
        **_summary(row).model_dump(),
        exit_code=row.get("exit_code"),
        error_message=row.get("error_message"),
        stages=_receipt_stages(row.get("run_receipt_json")),
    )


def _require_run(run_id: str) -> dict[str, Any]:
    """Load a run or distinguish it from an output/artifact not yet present."""

    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


def _validate_component(value: str, label: str) -> None:
    """Reject path syntax before owner/repository values influence any path or CLI call."""

    if (
        not value
        or value != value.strip()
        or Path(value).name != value
        or value in {".", ".."}
        or _COMPONENT_RE.fullmatch(value) is None
    ):
        raise HTTPException(
            status_code=422,
            detail="{0} must be a simple path-safe owner/repository component.".format(label),
        )


def _validate_request(request: CreateRunRequest) -> None:
    """Reuse the clone stage's URL acceptance boundary before queuing a run."""

    # Pydantic rejects this combination during normal HTTP decoding.  Keep the
    # same check at the endpoint boundary as defense in depth for any direct
    # Python caller that supplies a constructed model instance.
    if request.skip_triage and request.health_report_only:
        raise HTTPException(
            status_code=422,
            detail="skip_triage and health_report_only cannot both be true.",
        )
    try:
        _validate_repository_url(request.repo_url)
    except CloneError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_component(request.owner, "owner")
    _validate_component(request.repo, "repo")


def _output_root() -> Path:
    """Return the server-owned root containing isolated per-run directories."""

    configured = os.environ.get("LAZARUS_API_OUTPUT_ROOT")
    base = Path(configured).expanduser() if configured else Path.cwd() / "lazarus_runs"
    return base.resolve()


def _reserve_output_dir() -> tuple[str, Path]:
    """Create a fresh server-owned output directory before a queued row is persisted."""

    root = _output_root()
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        run_id = uuid.uuid4().hex
        output_dir = root / run_id
        if not _is_within(output_dir, root):  # defensive, even though UUIDs are path-safe
            raise RuntimeError("Generated run output directory escaped the configured output root.")
        try:
            output_dir.mkdir()
        except FileExistsError:
            continue
        return run_id, output_dir.resolve()
    raise RuntimeError("Could not reserve a fresh output directory for a new run.")


def _run_output_dir(run: dict[str, Any]) -> Path:
    """Return an existing run's server-owned output directory or a clear 404."""

    output_dir = Path(run["output_dir"])
    if not output_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run output directory does not exist yet.")
    return output_dir


def _artifact_candidate(output_dir: Path, artifact_path: str) -> Path:
    """Validate a requested relative artifact with the established resolve/containment check."""

    normalized = artifact_path.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not artifact_path
        or artifact_path.startswith(("/", "\\"))
        or Path(artifact_path).is_absolute()
        or PureWindowsPath(artifact_path).is_absolute()
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
    ):
        raise HTTPException(
            status_code=400,
            detail="Artifact path must be a relative path contained within the run output directory.",
        )
    candidate = output_dir.joinpath(*parts)
    if not _is_within(candidate, output_dir):
        raise HTTPException(
            status_code=400,
            detail="Artifact path must be contained within the run output directory.",
        )
    return candidate


def _media_type(path: Path) -> str:
    """Provide predictable text/JSON types for generated Lazarus artifacts."""

    if path.suffix.lower() == ".json":
        return "application/json"
    return _TEXT_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Provide an unauthenticated liveness probe for local deployment."""

    return HealthResponse(status="ok")


@app.post("/runs", response_model=RunSummary, status_code=202)
def create_run(request: CreateRunRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    """Queue one separately-process-isolated orchestrator invocation."""

    _validate_request(request)
    run_id, output_dir = _reserve_output_dir()
    try:
        row = store.create_run(
            run_id=run_id,
            repo_url=request.repo_url,
            github_owner=request.owner,
            github_repo=request.repo,
            ref_requested=request.ref,
            include_closed=request.include_closed,
            skip_triage=request.skip_triage,
            health_report_only=request.health_report_only,
            output_dir=output_dir,
        )
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail="Could not persist queued run: {0}".format(exc)) from exc
    background_tasks.add_task(runner.launch_run, row["id"])
    return JSONResponse(
        status_code=202,
        headers={"Location": "/runs/{0}".format(row["id"])},
        content=_summary(row).model_dump(mode="json"),
    )


@app.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel_run(run_id: str) -> RunDetail:
    """Terminate an active CLI process, or report that a terminal run cannot be cancelled."""

    _require_run(run_id)
    try:
        updated = runner.cancel_run(run_id)
    except runner.RunNotCancellable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:  # pragma: no cover - row deletion is not part of this API
        raise HTTPException(status_code=404, detail="Run not found.") from exc
    return _detail(updated)


@app.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str) -> RunDetail:
    """Return one persisted run and any receipt stages already cached by the poller."""

    return _detail(_require_run(run_id))


@app.get("/runs/{run_id}/artifacts", response_model=ArtifactList)
def list_artifacts(run_id: str) -> ArtifactList:
    """List every current regular file under an existing run directory."""

    output_dir = _run_output_dir(_require_run(run_id))
    root_resolved = output_dir.resolve()
    artifacts = [
        path.resolve().relative_to(root_resolved).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and _is_within(path, output_dir)
    ]
    return ArtifactList(run_id=run_id, artifacts=sorted(artifacts))


@app.get("/runs/{run_id}/artifacts/{artifact_path:path}")
def get_artifact(run_id: str, artifact_path: str) -> FileResponse:
    """Serve one contained generated artifact without permitting traversal or symlink escape."""

    output_dir = _run_output_dir(_require_run(run_id))
    candidate = _artifact_candidate(output_dir, artifact_path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Artifact not found yet.")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Artifact path does not name a regular file.")
    return FileResponse(candidate, media_type=_media_type(candidate), filename=candidate.name)


@app.get("/runs", response_model=list[RunSummary])
def list_runs(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[RunSummary]:
    """Return the newest persisted run summaries first."""

    return [_summary(row) for row in store.list_runs(limit=limit, offset=offset)]


def run_server() -> None:
    """Start the optional loopback-only API server; install the ``api`` extra first.

    The transport intentionally has no authentication.  Binding to loopback by
    default therefore keeps a development/demo server off the local network.
    An operator may set ``LAZARUS_API_HOST`` explicitly for a controlled,
    separately secured deployment.
    """

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - only reachable without the optional extra
        raise RuntimeError("lazarus-api requires `pip install lazarus-revival[api]`.") from exc
    try:
        port = int(os.environ.get("LAZARUS_API_PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("LAZARUS_API_PORT must be an integer.") from exc
    host = os.environ.get("LAZARUS_API_HOST", "127.0.0.1").strip()
    if not host:
        raise RuntimeError("LAZARUS_API_HOST must not be empty.")
    uvicorn.run(app, host=host, port=port)
