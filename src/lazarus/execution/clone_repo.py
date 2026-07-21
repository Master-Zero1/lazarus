"""Create a local, read-only Git checkout for Lazarus inventory stages.

This execution utility is intentionally limited to cloning a repository.  It
does not call the GitHub API, create or verify forks, read credentials, or make
any changes to the checked-out repository.  See ``directives/diagnose_repo.md``
and ``directives/generate_docs.md`` for the Layer 2 stages that consume the
resulting local path.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse


GIT_TIMEOUT_SECONDS = 120
_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


class CloneError(RuntimeError):
    """A handled cloning failure suitable for a JSON receipt."""


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    """Write a deterministic JSON receipt to a file or standard output."""
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        sys.stdout.write(rendered)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")


def _receipt(
    *,
    repository_url: str,
    local_path: str,
    ref_requested: str | None,
    clone_status: str,
    ref_resolved: str | None = None,
    origin_url: str | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build the stable receipt consumed by higher-level orchestration."""
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "repository_url": repository_url,
        "local_path": local_path,
        "ref_requested": ref_requested,
        "ref_resolved": ref_resolved,
        "origin_url": origin_url,
        "clone_status": clone_status,
        "warnings": warnings or [],
    }
    if error is not None:
        receipt["error"] = error
    return receipt


def _path_for_receipt(destination: str | Path) -> Path:
    """Resolve the requested destination without creating it."""
    return Path(destination).expanduser().resolve()


def _validate_repository_url(repository_url: str) -> None:
    """Reject local paths, SSH remotes, and URLs carrying credentials."""
    parsed = urlparse(repository_url)
    if parsed.scheme not in {"https", "git"} or not parsed.netloc:
        raise CloneError(
            "repository URL must be an https:// or git:// URL with a host; "
            "local paths and other URL schemes are not accepted."
        )
    if parsed.username is not None or parsed.password is not None:
        raise CloneError(
            "repository URL must not include credentials; this utility never "
            "accepts or injects authentication data."
        )


def _validate_destination(destination: Path) -> bool:
    """Validate the destination and return whether its empty directory existed."""
    if destination.is_symlink():
        raise CloneError("destination must not be a symbolic link.")
    if destination.exists() and not destination.is_dir():
        raise CloneError("destination exists but is not a directory.")
    if destination.exists() and any(destination.iterdir()):
        raise CloneError(
            "destination already exists and is not empty; refusing to merge "
            "into or overwrite existing content."
        )
    if not destination.parent.exists() or not destination.parent.is_dir():
        raise CloneError(
            "destination parent directory does not exist or is not a directory; "
            "create the parent before cloning."
        )
    return destination.exists()


def _git_environment() -> dict[str, str]:
    """Supply only minimal platform variables and explicitly disable prompts.

    The environment is constructed from an allowlist rather than copied from
    ``os.environ``.  In particular, it never reads or passes through GitHub
    tokens or any other credential variables.
    """
    environment: dict[str, str] = {}
    for variable in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
    ):
        value = os.environ.get(variable)
        if value:
            environment[variable] = value

    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


def _git_command(*arguments: str) -> list[str]:
    """Build a non-interactive Git command without credential helpers."""
    return [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        *arguments,
    ]


def _command_output(result: subprocess.CompletedProcess[str]) -> str:
    """Return Git's useful failure text, preferring stderr."""
    parts = [part.strip() for part in (result.stderr, result.stdout) if part and part.strip()]
    return "\n".join(parts) or "Git exited without diagnostic output."


def _timeout_output(exc: subprocess.TimeoutExpired) -> str:
    """Extract available process output from a timeout exception."""
    parts: list[str] = []
    for part in (exc.stderr, exc.stdout):
        if isinstance(part, bytes):
            part = part.decode("utf-8", errors="replace")
        if isinstance(part, str) and part.strip():
            parts.append(part.strip())
    return "\n".join(parts)


def _failure_message(operation: str, detail: str) -> str:
    """Turn Git diagnostics into a clear, non-interactive failure message."""
    lowered = detail.lower()
    auth_markers = (
        "authentication failed",
        "authentication is required",
        "could not read username",
        "terminal prompts disabled",
        "http basic: access denied",
        "permission denied",
    )
    if any(marker in lowered for marker in auth_markers):
        return (
            "Git could not authenticate to the repository. Use a publicly "
            "accessible https:// or git:// URL, or configure Git access outside "
            "Lazarus; this utility does not accept credentials. Git reported: "
            f"{detail}"
        )
    return f"Git {operation} failed: {detail}"


def _run_git(arguments: list[str], operation: str) -> subprocess.CompletedProcess[str]:
    """Run Git with a bounded timeout and convert expected failures to CloneError."""
    try:
        completed = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env=_git_environment(),
        )
    except FileNotFoundError as exc:
        raise CloneError("Git is not installed or is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        detail = _timeout_output(exc)
        suffix = f" Git reported before timing out: {detail}" if detail else ""
        raise CloneError(
            f"Git {operation} timed out after {GIT_TIMEOUT_SECONDS} seconds.{suffix}"
        ) from exc
    except OSError as exc:
        raise CloneError(f"Unable to start Git for {operation}: {exc}") from exc

    if completed.returncode != 0:
        raise CloneError(_failure_message(operation, _command_output(completed)))
    return completed


def _remove_partial_destination(destination: Path, existed_before_clone: bool) -> list[str]:
    """Remove only clone output after a failed clone, retaining an empty input dir."""
    if not destination.exists() and not destination.is_symlink():
        return []

    try:
        if not existed_before_clone:
            if destination.is_symlink() or not destination.is_dir():
                destination.unlink()
            else:
                shutil.rmtree(destination)
            return []

        for child in destination.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        return []
    except OSError as exc:
        return [f"Could not fully clean partial clone output at {destination}: {exc}"]


def clone_repository(
    repository_url: str,
    destination: str | Path,
    ref: str | None = None,
) -> dict[str, Any]:
    """Clone a public Git URL and return a receipt without raising Git failures.

    A branch or tag uses a shallow one-ref clone.  A SHA uses a full clone so
    Git can resolve commits beyond the default branch's shallow history.
    """
    try:
        resolved_destination = _path_for_receipt(destination)
        local_path = str(resolved_destination)
    except (OSError, RuntimeError) as exc:
        return _receipt(
            repository_url=repository_url,
            local_path=str(destination),
            ref_requested=ref,
            clone_status="failed",
            error=f"Could not resolve destination path: {exc}",
        )

    existed_before_clone = False
    clone_attempted = False
    try:
        _validate_repository_url(repository_url)
        existed_before_clone = _validate_destination(resolved_destination)

        if ref and _COMMIT_SHA_PATTERN.fullmatch(ref):
            clone_arguments = _git_command("clone", repository_url, str(resolved_destination))
        elif ref:
            clone_arguments = _git_command(
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                repository_url,
                str(resolved_destination),
            )
        else:
            clone_arguments = _git_command(
                "clone",
                "--depth",
                "1",
                repository_url,
                str(resolved_destination),
            )

        clone_attempted = True
        _run_git(clone_arguments, "clone")

        if ref and _COMMIT_SHA_PATTERN.fullmatch(ref):
            _run_git(
                _git_command("-C", str(resolved_destination), "checkout", "--detach", ref),
                "checkout",
            )

        resolved = _run_git(
            _git_command("-C", str(resolved_destination), "rev-parse", "HEAD"),
            "resolve the checked-out revision",
        ).stdout.strip()
        if not _COMMIT_SHA_PATTERN.fullmatch(resolved):
            raise CloneError("Git returned an invalid commit SHA for the checked-out revision.")

        # The orchestration layer uses the actual configured remote—not merely
        # the operator-supplied clone argument—to bind this checkout to the
        # GitHub issue/PR target before it invokes later stages.
        origin_url = _run_git(
            _git_command("-C", str(resolved_destination), "remote", "get-url", "origin"),
            "read the cloned origin URL",
        ).stdout.strip()
        if not origin_url:
            raise CloneError("Git did not report a remote origin URL for the cloned checkout.")

        return _receipt(
            repository_url=repository_url,
            local_path=local_path,
            ref_requested=ref,
            ref_resolved=resolved,
            origin_url=origin_url,
            clone_status="cloned",
        )
    except CloneError as exc:
        warnings = (
            _remove_partial_destination(resolved_destination, existed_before_clone)
            if clone_attempted
            else []
        )
        return _receipt(
            repository_url=repository_url,
            local_path=local_path,
            ref_requested=ref,
            clone_status="failed",
            warnings=warnings,
            error=str(exc),
        )
    except Exception as exc:  # Defensive receipt: Git failures must not crash a pipeline stage.
        warnings = (
            _remove_partial_destination(resolved_destination, existed_before_clone)
            if clone_attempted
            else []
        )
        return _receipt(
            repository_url=repository_url,
            local_path=local_path,
            ref_requested=ref,
            clone_status="failed",
            warnings=warnings,
            error=f"Unexpected clone utility failure: {exc}",
        )


def main(argv: list[str] | None = None) -> int:
    """Run the read-only clone CLI and emit one JSON receipt on every outcome."""
    parser = argparse.ArgumentParser(description="Create a bounded, read-only local Git clone.")
    parser.add_argument("repo_url", help="Public https:// or git:// repository URL to clone.")
    parser.add_argument("--dest", required=True, help="Empty or non-existent checkout directory.")
    parser.add_argument("--ref", help="Optional branch, tag, or commit SHA to check out.")
    parser.add_argument("--output", help="Optional path for the JSON clone receipt.")
    arguments = parser.parse_args(argv)

    receipt = clone_repository(arguments.repo_url, arguments.dest, arguments.ref)
    output_path = Path(arguments.output) if arguments.output else None
    try:
        _write_json(receipt, output_path)
    except OSError as exc:
        sys.stderr.write(f"Could not write clone receipt: {exc}\n")
        return 1
    return 0 if receipt["clone_status"] == "cloned" else 1


if __name__ == "__main__":
    raise SystemExit(main())
