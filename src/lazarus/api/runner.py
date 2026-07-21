"""Background subprocess launching and receipt-aware polling for the HTTP API.

The API deliberately invokes the installed ``lazarus`` console command rather
than importing ``run_pipeline()``.  Each API run therefore has the same
process isolation as a human terminal invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import store


POLL_INTERVAL_SECONDS = 2.0
_PROCESS_LOCK = threading.RLock()
_PROCESSES: dict[str, "_TrackedProcess"] = {}
_POLLER_THREAD: threading.Thread | None = None
_POLLER_STOP = threading.Event()


class RunNotCancellable(RuntimeError):
    """Raised when a run has already reached a terminal state."""


class _ProcessHandle(Protocol):
    """The small ``Popen`` surface needed by the poller and cancellation path."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class _TrackedProcess:
    """An active process, with whether its exit code is recoverable."""

    process: _ProcessHandle
    recovered_after_restart: bool = False


class _RecoveredProcess:
    """Best-effort liveness wrapper for a PID persisted before an API restart.

    Python cannot reconstruct a real ``Popen`` object after restart.  This
    wrapper can determine whether the PID remains alive and lets the poller
    finish from a real run receipt when one appears.  Its exit code is never
    presented as authoritative.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None if _pid_is_running(self.pid) else 1

    def terminate(self) -> None:
        os.kill(self.pid, signal.SIGTERM)

    def kill(self) -> None:
        os.kill(self.pid, getattr(signal, "SIGKILL", signal.SIGTERM))

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("lazarus", timeout)
            time.sleep(0.1)
        return 1


def _pid_is_running(pid: int) -> bool:
    """Check PID liveness without a third-party process-inspection dependency."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # It exists but is not inspectable by this process; treat it as live.
        return True
    except OSError:
        return False
    return True


def _lazarus_executable() -> str:
    """Find the same console entry point an operator would invoke manually."""

    configured = os.environ.get("LAZARUS_CLI")
    if configured:
        return configured
    discovered = shutil.which("lazarus")
    if discovered:
        return discovered
    executable_name = "lazarus.exe" if os.name == "nt" else "lazarus"
    sibling = Path(sys.executable).resolve().parent / executable_name
    if sibling.is_file():
        return str(sibling)
    return "lazarus"


def _command_for_run(run: dict[str, Any]) -> list[str]:
    """Render exactly the documented top-level ``lazarus`` CLI arguments."""

    command = [
        _lazarus_executable(),
        str(run["repo_url"]),
        "--owner",
        str(run["github_owner"]),
        "--repo",
        str(run["github_repo"]),
        "--output-dir",
        str(run["output_dir"]),
    ]
    if run.get("ref_requested"):
        command.extend(["--ref", str(run["ref_requested"])])
    if run.get("include_closed"):
        command.append("--include-closed")
    if run.get("skip_triage"):
        command.append("--skip-triage")
    if run.get("health_report_only"):
        command.append("--health-report-only")
    return command


def _stderr_summary(output_dir: Path) -> str:
    """Read a bounded error tail after a process exits without retaining logs in RAM."""

    try:
        content = (output_dir / "stderr.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return content[-8192:].strip()


def _read_receipt(output_dir: Path) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Return raw receipt text, parsed receipt, and a parse error if any."""

    receipt_path = output_dir / "run_receipt.json"
    if not receipt_path.is_file():
        return None, None, None
    try:
        raw = receipt_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, None, "Could not read run_receipt.json: {0}".format(exc)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return raw, None, "run_receipt.json is not valid JSON: {0}".format(exc)
    if not isinstance(parsed, dict):
        return raw, None, "run_receipt.json is not a JSON object."
    return raw, parsed, None


def _halt_message(receipt: dict[str, Any], fallback: str) -> str:
    """Preserve the orchestrator's halt evidence in the API's concise error field."""

    error = receipt.get("error")
    stage = receipt.get("halted_stage")
    if isinstance(error, str) and error:
        return "halted at {0}: {1}".format(stage, error) if isinstance(stage, str) and stage else error
    return fallback


def _finalize_process(run_id: str, exit_code: int | None, *, recovered_after_restart: bool) -> None:
    """Persist a terminal result only when the database still says ``running``."""

    run = store.get_run(run_id)
    if run is None or run["status"] != "running":
        return

    output_dir = Path(run["output_dir"])
    raw_receipt, receipt, receipt_error = _read_receipt(output_dir)
    fields: dict[str, Any] = {
        "finished_at": store.utc_now(),
        "exit_code": None if recovered_after_restart else exit_code,
        "process_pid": None,
    }
    if raw_receipt is not None:
        fields["run_receipt_json"] = raw_receipt

    stderr = _stderr_summary(output_dir)
    if receipt_error:
        fields.update(
            status="error",
            error_message=receipt_error if not stderr else "{0} {1}".format(receipt_error, stderr),
        )
    elif receipt is None:
        message = "Orchestrator exited without producing run_receipt.json."
        if exit_code is not None:
            message = "Orchestrator exited with code {0} without producing run_receipt.json.".format(exit_code)
        fields.update(status="error", error_message=stderr or message)
    else:
        receipt_status = receipt.get("status")
        if receipt_status == "completed" and (recovered_after_restart or exit_code == 0):
            fields.update(status="completed", error_message=None)
        elif receipt_status == "halted":
            fields.update(
                status="halted",
                error_message=_halt_message(receipt, stderr or "The orchestrator halted without an error message."),
            )
        elif receipt_status == "completed":
            fields.update(
                status="error",
                error_message=(
                    "run_receipt.json reports completed, but the orchestrator exited with code {0}. {1}".format(
                        exit_code, stderr
                    ).strip()
                ),
            )
        else:
            fields.update(
                status="error",
                error_message="run_receipt.json reported unexpected status {0!r}. {1}".format(receipt_status, stderr).strip(),
            )
    store.update_run(run_id, **fields)


def launch_run(run_id: str) -> None:
    """Launch one queued run non-blockingly through the installed CLI executable."""

    run = store.get_run(run_id)
    if run is None or run["status"] != "queued":
        return

    output_dir = Path(run["output_dir"])
    try:
        if not output_dir.is_dir():
            raise OSError("Run output directory is missing: {0}".format(output_dir))
        command = _command_for_run(run)
        stdout_path = output_dir / "stdout.log"
        stderr_path = output_dir / "stderr.log"
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
            )
    except OSError as exc:
        store.update_run(
            run_id,
            status="error",
            finished_at=store.utc_now(),
            error_message="Could not launch lazarus CLI: {0}".format(exc),
            process_pid=None,
        )
        return

    with _PROCESS_LOCK:
        latest = store.get_run(run_id)
        if latest is None or latest["status"] != "queued":
            # A cancellation won the race while the subprocess was being
            # created.  Do not resurrect the queued row as ``running``.
            process.terminate()
            return
        store.update_run(
            run_id,
            status="running",
            started_at=store.utc_now(),
            process_pid=process.pid,
            error_message=None,
        )
        _PROCESSES[run_id] = _TrackedProcess(process=process)


def poll_once() -> None:
    """Check all tracked subprocesses once and cache terminal receipts."""

    with _PROCESS_LOCK:
        tracked_processes = list(_PROCESSES.items())
    for run_id, tracked in tracked_processes:
        exit_code = tracked.process.poll()
        if exit_code is None:
            continue
        with _PROCESS_LOCK:
            if _PROCESSES.get(run_id) is not tracked:
                continue
            _PROCESSES.pop(run_id, None)
        _finalize_process(
            run_id,
            exit_code,
            recovered_after_restart=tracked.recovered_after_restart,
        )


def _poll_loop() -> None:
    """Run the lightweight watcher until API shutdown requests a stop."""

    while not _POLLER_STOP.wait(POLL_INTERVAL_SECONDS):
        poll_once()


def recover_running_runs() -> None:
    """Reattach to live stored PIDs or resolve stale running records safely."""

    for run in store.list_runs_by_status("running"):
        run_id = str(run["id"])
        output_dir = Path(run["output_dir"])
        raw_receipt, receipt, receipt_error = _read_receipt(output_dir)
        if receipt is not None and receipt.get("status") in {"completed", "halted"}:
            _finalize_process(run_id, None, recovered_after_restart=True)
            continue
        pid = run.get("process_pid")
        if isinstance(pid, int) and _pid_is_running(pid):
            with _PROCESS_LOCK:
                _PROCESSES[run_id] = _TrackedProcess(
                    process=_RecoveredProcess(pid), recovered_after_restart=True
                )
            continue
        message = "API server restarted while this run was in progress; its final state could not be determined."
        if receipt_error:
            message = "{0} {1}".format(message, receipt_error)
        fields: dict[str, Any] = {
            "status": "error",
            "finished_at": store.utc_now(),
            "error_message": message,
            "process_pid": None,
        }
        if raw_receipt is not None:
            fields["run_receipt_json"] = raw_receipt
        store.update_run(run_id, **fields)


def start_poller() -> None:
    """Start one daemon poller and repair stale in-progress records at startup."""

    global _POLLER_THREAD
    recover_running_runs()
    with _PROCESS_LOCK:
        if _POLLER_THREAD is not None and _POLLER_THREAD.is_alive():
            return
        _POLLER_STOP.clear()
        _POLLER_THREAD = threading.Thread(
            target=_poll_loop,
            name="lazarus-api-poller",
            daemon=True,
        )
        _POLLER_THREAD.start()


def stop_poller() -> None:
    """Stop the API-owned watcher without terminating independent pipeline runs."""

    global _POLLER_THREAD
    _POLLER_STOP.set()
    thread = _POLLER_THREAD
    if thread is not None:
        thread.join(timeout=POLL_INTERVAL_SECONDS + 1)
    _POLLER_THREAD = None


def cancel_run(run_id: str) -> dict[str, Any]:
    """Best-effort cancellation with a short terminate-then-kill grace period."""

    with _PROCESS_LOCK:
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] not in {"queued", "running"}:
            raise RunNotCancellable("Run has already finished and cannot be cancelled.")
        if run["status"] == "queued":
            updated = store.update_run(
                run_id,
                status="error",
                finished_at=store.utc_now(),
                error_message="cancelled by operator",
                process_pid=None,
            )
            if updated is None:  # pragma: no cover - defensive concurrent deletion guard
                raise KeyError(run_id)
            return updated
        tracked = _PROCESSES.pop(run_id, None)
    if tracked is None:
        raise RunNotCancellable("Run process is no longer controllable by this API process.")
    if tracked.process.poll() is not None:
        _finalize_process(
            run_id,
            tracked.process.poll(),
            recovered_after_restart=tracked.recovered_after_restart,
        )
        raise RunNotCancellable("Run has already finished and cannot be cancelled.")

    exit_code: int | None = None
    try:
        tracked.process.terminate()
        try:
            exit_code = tracked.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tracked.process.kill()
            exit_code = tracked.process.wait(timeout=2)
    except OSError:
        # The operator's intent is still recorded; a concurrently exited process
        # should not become a stale ``running`` record.
        exit_code = tracked.process.poll()

    updated = store.update_run(
        run_id,
        status="error",
        finished_at=store.utc_now(),
        exit_code=None if tracked.recovered_after_restart else exit_code,
        error_message="cancelled by operator",
        process_pid=None,
    )
    if updated is None:  # pragma: no cover - defensive concurrent deletion guard
        raise KeyError(run_id)
    return updated
