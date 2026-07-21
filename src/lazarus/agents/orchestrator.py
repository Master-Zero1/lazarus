"""Run the Lazarus revival pipeline from one resolved local Git checkout.

Directive: ``directives/run_pipeline.md``.  This Layer 2 orchestrator starts
with the read-only ``clone_repo.py`` execution utility, then invokes the
existing independently runnable agents in their existing order.  It never
passes ``--execute`` or operator approval to ``pr_agent.py``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from ._artifact_identity import (
    ArtifactIdentityError,
    identity_from_clone_receipt,
    identity_from_json,
    identity_from_local_path,
    require_same_repository,
)
from ._orchestration import PACKAGE_ROOT, call_execution, read_directive


DIRECTIVE = "run_pipeline.md"
AGENT_SCRIPTS = {
    "diagnosis_agent.py",
    "docs_agent.py",
    "triage_agent.py",
    "pr_agent.py",
    "synthesis_agent.py",
}
REQUIRED_DIRECTIVE_REFERENCES = (
    "clone_repo.py",
    "diagnosis_agent.py",
    "docs_agent.py",
    "fetch_issues.py",
    "fetch_prs.py",
    "triage_agent.py",
    "pr_agent.py",
    "synthesis_agent.py",
)


def _validate_directive() -> None:
    """Confirm the trusted pipeline SOP names every invoked stage."""
    directive = read_directive(DIRECTIVE)
    missing = [reference for reference in REQUIRED_DIRECTIVE_REFERENCES if reference not in directive]
    if missing:
        raise RuntimeError(
            "Pipeline SOP does not name required stage(s): {0}.".format(", ".join(missing))
        )


def call_agent(script_name: str, arguments: Sequence[str]) -> str:
    """Invoke one sibling Layer 2 script without a shell or implicit arguments."""
    if script_name not in AGENT_SCRIPTS or Path(script_name).name != script_name:
        raise ValueError("Unknown Layer 2 agent script: {0}".format(script_name))
    script_path = Path(__file__).resolve().parent / script_name
    if not script_path.is_file():
        raise FileNotFoundError("Layer 2 agent script is missing: {0}".format(script_path))

    completed = subprocess.run(
        [sys.executable, "-m", "lazarus.agents.{0}".format(Path(script_name).stem), *arguments],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "No output returned."
        raise RuntimeError("Agent script failed: {0}: {1}".format(script_name, detail))
    return completed.stdout


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one deterministic JSON receipt outside the cloned repository."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    """Load a required JSON artifact and reject non-object content."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not read {0}: {1}".format(label, exc)) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("{0} is not a JSON object: {1}".format(label, path))
    return payload


def _require_file(path: Path, label: str) -> None:
    """Fail the producing stage when it did not leave its promised artifact."""
    if not path.is_file():
        raise RuntimeError("{0} was not created: {1}".format(label, path))


def _path_string(path: Path) -> str:
    """Return one absolute artifact path for the top-level receipt."""
    return str(path.resolve())


def _validate_output_dir(output_dir: Path) -> None:
    """Keep pipeline artifacts out of a pre-existing repository checkout root."""
    git_marker = output_dir / ".git"
    if output_dir.exists() and (git_marker.is_dir() or git_marker.is_file()):
        raise ValueError("--output-dir is an existing Git checkout and cannot receive pipeline artifacts: {0}".format(output_dir))


def _record_stage(
    receipt: dict[str, Any],
    name: str,
    status: str,
    *,
    artifacts: dict[str, Path] | None = None,
    detail: str | None = None,
) -> None:
    """Append a stage outcome and add its actual artifact paths to the receipt."""
    stage: dict[str, Any] = {"stage": name, "status": status}
    if detail:
        stage["detail"] = detail
    if artifacts:
        rendered = {label: _path_string(path) for label, path in artifacts.items()}
        stage["artifacts"] = rendered
        receipt["artifacts"].update(rendered)
    receipt["stages"].append(stage)


def _write_run_receipt(output_dir: Path, receipt: dict[str, Any]) -> Path:
    """Persist the pipeline's own receipt after every terminal outcome."""
    path = output_dir / "run_receipt.json"
    _write_json(path, receipt)
    return path


def _halt(
    output_dir: Path,
    receipt: dict[str, Any],
    stage: str,
    error: str,
    *,
    artifacts: dict[str, Path] | None = None,
) -> tuple[dict[str, Any], int]:
    """Write an immediate, stage-specific halted run receipt and stop."""
    _record_stage(receipt, stage, "halted", artifacts=artifacts, detail=error)
    receipt["status"] = "halted"
    receipt["halted_stage"] = stage
    receipt["error"] = error
    _write_run_receipt(output_dir, receipt)
    return receipt, 1


def _intentional_partial(
    output_dir: Path,
    receipt: dict[str, Any],
    reason: str,
    not_attempted: list[str],
) -> tuple[dict[str, Any], int]:
    """Finish a requested partial run without misrepresenting it as a failure."""
    receipt["status"] = "completed"
    receipt["completion_scope"] = "intentional_partial"
    receipt["intentional_stop_reason"] = reason
    receipt["stages_not_attempted_by_design"] = not_attempted
    _write_run_receipt(output_dir, receipt)
    return receipt, 0


def _clone_receipt_from_failure(error: RuntimeError) -> dict[str, Any] | None:
    """Recover clone_repo.py's documented failed receipt from call_execution's error."""
    marker = "Execution script failed: clone_repo.py:"
    detail = str(error)
    if not detail.startswith(marker):
        return None
    try:
        payload = json.loads(detail[len(marker) :].strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _run_clone(repo_url: str, destination: Path, ref: str | None) -> dict[str, Any]:
    """Use the shared Layer 3 helper while preserving clone failures as receipts."""
    arguments = [repo_url, "--dest", str(destination)]
    if ref is not None:
        arguments.extend(["--ref", ref])
    try:
        receipt = call_execution("clone_repo.py", arguments)
    except RuntimeError as exc:
        failed_receipt = _clone_receipt_from_failure(exc)
        if failed_receipt is not None:
            return failed_receipt
        raise
    if not isinstance(receipt, dict):  # Defensive: call_execution currently guarantees this.
        raise RuntimeError("clone_repo.py returned a non-object receipt.")
    return receipt


def _is_preview_halt(path: Path) -> bool:
    """Identify the explicit local preview halt receipt emitted by pr_agent.py."""
    if not path.is_file():
        return False
    try:
        payload = _read_json_object(path, "draft-PR preview receipt")
    except RuntimeError:
        return False
    return payload.get("mode") == "preview" and payload.get("status") == "halted"


def _initial_receipt(
    repo_url: str,
    owner: str,
    repository: str,
    ref: str | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Build the run-level receipt before stage zero starts."""
    return {
        "schema_version": 1,
        "status": "running",
        "repository_url": repo_url,
        "github_repository": "{0}/{1}".format(owner, repository),
        "ref_requested": ref,
        "resolved_commit_sha": None,
        "output_dir": _path_string(output_dir),
        "stages": [],
        "artifacts": {},
    }


def run_pipeline(
    repo_url: str,
    *,
    owner: str,
    repository: str,
    output_dir: Path,
    ref: str | None = None,
    skip_triage: bool = False,
    health_report_only: bool = False,
    include_closed: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run the directive sequence, halting immediately after any failed stage."""
    _validate_directive()
    output_dir = output_dir.resolve()
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("--output-dir exists but is not a directory: {0}".format(output_dir))
    _validate_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = _initial_receipt(repo_url, owner, repository, ref, output_dir)

    if Path(repository).name != repository or repository in {".", ".."}:
        raise ValueError("--repo must be a single repository-name path component.")
    expected_upstream = "{0}/{1}".format(owner, repository)
    clone_root = output_dir / "clone"
    clone_root.mkdir(parents=True, exist_ok=True)
    clone_dir = clone_root / repository
    clone_receipt_path = output_dir / "clone_receipt.json"
    try:
        clone_receipt = _run_clone(repo_url, clone_dir, ref)
        _write_json(clone_receipt_path, clone_receipt)
    except (RuntimeError, ValueError, OSError) as exc:
        return _halt(output_dir, receipt, "clone", str(exc))

    if clone_receipt.get("clone_status") != "cloned":
        error = clone_receipt.get("error")
        message = error if isinstance(error, str) and error else "clone_repo.py did not report clone_status 'cloned'."
        return _halt(
            output_dir,
            receipt,
            "clone",
            message,
            artifacts={"clone_receipt": clone_receipt_path},
        )

    resolved_commit = clone_receipt.get("ref_resolved")
    if not isinstance(resolved_commit, str) or not resolved_commit:
        return _halt(
            output_dir,
            receipt,
            "clone",
            "clone_repo.py reported success without a resolved commit SHA.",
            artifacts={"clone_receipt": clone_receipt_path},
        )
    local_path = clone_receipt.get("local_path")
    cloned_path = Path(local_path) if isinstance(local_path, str) else clone_dir
    if not cloned_path.is_dir():
        return _halt(
            output_dir,
            receipt,
            "clone",
            "clone_repo.py reported success but the local checkout directory is unavailable.",
            artifacts={"clone_receipt": clone_receipt_path},
        )
    try:
        clone_identity = require_same_repository(
            identity_from_clone_receipt(
                clone_receipt,
                "orchestrated clone receipt",
                expected_upstream=expected_upstream,
            ),
            identity_from_local_path(
                cloned_path,
                "orchestrated local checkout",
                expected_upstream=expected_upstream,
            ),
            identity_from_json(
                {"repository": expected_upstream},
                "orchestrator GitHub target",
                require_remote=True,
            ),
        )
    except ArtifactIdentityError as exc:
        return _halt(
            output_dir,
            receipt,
            "clone",
            str(exc),
            artifacts={"clone_receipt": clone_receipt_path},
        )
    receipt["resolved_commit_sha"] = resolved_commit
    receipt["local_checkout"] = _path_string(cloned_path)
    receipt["repository_identity"] = {
        "expected_upstream": expected_upstream,
        "origin_url": clone_receipt.get("origin_url"),
        "verification_level": clone_identity.verification_level,
    }
    _record_stage(
        receipt,
        "clone",
        "completed",
        artifacts={"clone_receipt": clone_receipt_path, "local_checkout": cloned_path},
    )

    findings_dir = output_dir / "diagnosis_findings"
    health_report = output_dir / "health_report.md"
    try:
        call_agent(
            "diagnosis_agent.py",
            [
                str(cloned_path),
                "--findings-dir",
                str(findings_dir),
                "--expected-upstream",
                expected_upstream,
                "--repo-owner",
                owner,
                "--repo-name",
                repository,
                "--output",
                str(health_report),
            ],
        )
        _require_file(health_report, "Health Report")
    except (RuntimeError, ValueError, OSError) as exc:
        return _halt(output_dir, receipt, "diagnose", str(exc))
    _record_stage(
        receipt,
        "diagnose",
        "completed",
        artifacts={"health_report": health_report, "diagnosis_findings": findings_dir},
    )

    if health_report_only:
        return _intentional_partial(
            output_dir,
            receipt,
            "--health-report-only requested; clone and diagnosis completed by design.",
            ["generate_docs", "triage", "draft_pr_preview", "synthesize"],
        )

    docs_draft_dir = output_dir / "docs_draft"
    try:
        call_agent(
            "docs_agent.py",
            [
                str(cloned_path),
                "--health-report",
                str(health_report),
                "--repo-identity",
                expected_upstream,
                "--output-dir",
                str(docs_draft_dir),
            ],
        )
        for filename in (
            "README.md",
            "ARCHITECTURE.md",
            "CONTRIBUTING.md",
            "documentation_evidence.md",
            "code_structure_inventory.json",
        ):
            _require_file(docs_draft_dir / filename, "Documentation artifact {0}".format(filename))
    except (RuntimeError, ValueError, OSError) as exc:
        return _halt(output_dir, receipt, "generate_docs", str(exc))
    _record_stage(receipt, "generate_docs", "completed", artifacts={"docs_draft": docs_draft_dir})

    issues_snapshot = output_dir / "issues_snapshot.json"
    prs_snapshot = output_dir / "prs_snapshot.json"
    triage_report = output_dir / "triage_report.md"
    if not skip_triage:
        try:
            snapshot_state = "all" if include_closed else "open"
            issues = call_execution("fetch_issues.py", [owner, repository, "--state", snapshot_state])
            _write_json(issues_snapshot, issues)
            pull_requests = call_execution("fetch_prs.py", [owner, repository, "--state", snapshot_state])
            _write_json(prs_snapshot, pull_requests)
            call_agent(
                "triage_agent.py",
                [
                    "--issues-snapshot",
                    str(issues_snapshot),
                    "--prs-snapshot",
                    str(prs_snapshot),
                    "--health-report",
                    str(health_report),
                    "--output",
                    str(triage_report),
                    *( ["--include-closed"] if include_closed else [] ),
                ],
            )
            _require_file(triage_report, "Triage Report")
        except (RuntimeError, ValueError, OSError) as exc:
            partial_artifacts = {
                label: path
                for label, path in {
                    "issues_snapshot": issues_snapshot,
                    "pull_requests_snapshot": prs_snapshot,
                }.items()
                if path.is_file()
            }
            return _halt(output_dir, receipt, "triage", str(exc), artifacts=partial_artifacts)
        _record_stage(
            receipt,
            "triage",
            "completed",
            artifacts={
                "issues_snapshot": issues_snapshot,
                "pull_requests_snapshot": prs_snapshot,
                "triage_report": triage_report,
            },
        )

    preview_path = output_dir / "draft_pr_preview.json"
    preview_arguments = [
        str(docs_draft_dir),
        "--fork-owner",
        owner,
        "--fork-repo",
        repository,
        "--expected-upstream",
        expected_upstream,
        "--health-report",
        str(health_report),
        "--docs-evidence-path",
        str(docs_draft_dir / "documentation_evidence.md"),
        "--output",
        str(preview_path),
    ]
    try:
        # This argument list is deliberately fixed: it contains neither
        # --execute nor --operator-approval, making a live PR impossible here.
        call_agent("pr_agent.py", preview_arguments)
        preview_payload = _read_json_object(preview_path, "draft-PR preview receipt")
        if preview_payload.get("mode") != "preview":
            raise RuntimeError("pr_agent.py did not produce a preview-mode receipt.")
        if preview_payload.get("status") == "halted":
            _record_stage(
                receipt,
                "draft_pr_preview",
                "preview_halted",
                artifacts={"draft_pr_preview": preview_path},
                detail=str(preview_payload.get("error") or "Preview halted by pr_agent.py."),
            )
        else:
            _record_stage(
                receipt,
                "draft_pr_preview",
                "preview_generated",
                artifacts={"draft_pr_preview": preview_path},
            )
    except RuntimeError as exc:
        if _is_preview_halt(preview_path):
            preview_payload = _read_json_object(preview_path, "draft-PR preview receipt")
            _record_stage(
                receipt,
                "draft_pr_preview",
                "preview_halted",
                artifacts={"draft_pr_preview": preview_path},
                detail=str(preview_payload.get("error") or exc),
            )
        else:
            return _halt(output_dir, receipt, "draft_pr_preview", str(exc))
    except (ValueError, OSError) as exc:
        return _halt(output_dir, receipt, "draft_pr_preview", str(exc))

    if skip_triage:
        return _intentional_partial(
            output_dir,
            receipt,
            "--skip-triage requested; synthesis was not attempted because it requires a triage report.",
            ["triage", "synthesize"],
        )

    revival_report = output_dir / "revival_report.md"
    synthesis_arguments = [
        "--health-report",
        str(health_report),
        "--documentation-draft-dir",
        str(docs_draft_dir),
        "--triage-report",
        str(triage_report),
        "--draft-pr-receipt",
        str(preview_path),
        "--output",
        str(revival_report),
    ]
    try:
        call_agent("synthesis_agent.py", synthesis_arguments)
        _require_file(revival_report, "Revival Report")
    except (RuntimeError, ValueError, OSError) as exc:
        return _halt(output_dir, receipt, "synthesize", str(exc))
    _record_stage(receipt, "synthesize", "completed", artifacts={"revival_report": revival_report})

    receipt["status"] = "completed"
    receipt["completion_scope"] = "full"
    _write_run_receipt(output_dir, receipt)
    return receipt, 0


def main(argv: list[str] | None = None) -> int:
    """Run the directive-controlled pipeline and emit the top-level receipt path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_url", help="Public https:// or git:// repository URL to clone once.")
    parser.add_argument("--owner", required=True, help="GitHub owner for read-only issue/PR snapshots.")
    parser.add_argument("--repo", required=True, help="GitHub repository name for read-only snapshots.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for checkout and all pipeline artifacts.")
    parser.add_argument("--ref", help="Optional branch, tag, or commit SHA to clone.")
    parser.add_argument("--include-closed", action="store_true", help="Fetch and classify closed issues and pull requests in addition to the default open scope.")
    partial = parser.add_mutually_exclusive_group()
    partial.add_argument("--skip-triage", action="store_true", help="Intentionally omit triage and synthesis.")
    partial.add_argument("--health-report-only", action="store_true", help="Intentionally stop after clone and diagnosis.")
    args = parser.parse_args(argv)

    try:
        receipt, exit_code = run_pipeline(
            args.repo_url,
            owner=args.owner,
            repository=args.repo,
            output_dir=args.output_dir,
            ref=args.ref,
            skip_triage=args.skip_triage,
            health_report_only=args.health_report_only,
            include_closed=args.include_closed,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    sys.stdout.write(
        json.dumps(
            {
                "status": receipt["status"],
                "run_receipt": _path_string(args.output_dir.resolve() / "run_receipt.json"),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
