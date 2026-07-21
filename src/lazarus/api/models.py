"""Pydantic schemas for the thin Lazarus HTTP API transport layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


RunStatus = Literal["queued", "running", "completed", "halted", "error"]


class CreateRunRequest(BaseModel):
    """Parameters accepted when an operator queues a Lazarus pipeline run."""

    repo_url: str
    owner: str
    repo: str
    ref: str | None = None
    include_closed: bool = False
    skip_triage: bool = False
    health_report_only: bool = False

    @model_validator(mode="after")
    def _validate_partial_scope(self) -> "CreateRunRequest":
        """Reject the CLI's mutually exclusive partial-run choices at the API edge."""

        if self.skip_triage and self.health_report_only:
            raise ValueError("skip_triage and health_report_only cannot both be true.")
        return self


class RunStage(BaseModel):
    """A stage entry surfaced unchanged from an orchestrator run receipt."""

    stage: str
    status: str


class RunSummary(BaseModel):
    """The persisted run fields suitable for list and create responses."""

    id: str
    status: RunStatus
    repo_url: str
    github_owner: str
    github_repo: str
    created_at: str
    started_at: str | None
    finished_at: str | None


class RunDetail(RunSummary):
    """A run summary augmented with process outcome and receipt stages."""

    exit_code: int | None
    error_message: str | None
    stages: list[RunStage]


class ArtifactList(BaseModel):
    """The current regular files under one server-owned run directory."""

    run_id: str
    artifacts: list[str]


class HealthResponse(BaseModel):
    """The unauthenticated local liveness response."""

    status: Literal["ok"]
