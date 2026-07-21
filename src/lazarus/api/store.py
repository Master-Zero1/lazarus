"""SQLite persistence for Lazarus API pipeline runs.

The API deliberately uses the standard library's ``sqlite3`` module rather
than an ORM.  Each operation opens its own short-lived connection, so request
handlers and background run watchers do not share mutable connection state.
WAL mode and a busy timeout make concurrent readers and writers practical for
the small amount of metadata this service stores.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


RUN_STATUSES = frozenset({"queued", "running", "completed", "halted", "error"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    repo_url TEXT NOT NULL,
    github_owner TEXT NOT NULL,
    github_repo TEXT NOT NULL,
    ref_requested TEXT,
    include_closed INTEGER NOT NULL DEFAULT 0,
    skip_triage INTEGER NOT NULL DEFAULT 0,
    health_report_only INTEGER NOT NULL DEFAULT 0,
    output_dir TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    error_message TEXT,
    run_receipt_json TEXT,
    process_pid INTEGER
)
"""

_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED_DATABASES: set[Path] = set()

_BOOLEAN_COLUMNS = {"include_closed", "skip_triage", "health_report_only"}
_TEXT_COLUMNS = {
    "status",
    "repo_url",
    "github_owner",
    "github_repo",
    "ref_requested",
    "output_dir",
    "created_at",
    "started_at",
    "finished_at",
    "error_message",
    "run_receipt_json",
}
_NULLABLE_TEXT_COLUMNS = {
    "ref_requested",
    "started_at",
    "finished_at",
    "error_message",
    "run_receipt_json",
}
_UPDATEABLE_COLUMNS = _BOOLEAN_COLUMNS | _TEXT_COLUMNS | {"exit_code", "process_pid"}


def utc_now() -> str:
    """Return the current UTC time in ISO 8601 form for persisted metadata."""

    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    """Return the configured SQLite database path, resolved to an absolute path.

    ``LAZARUS_API_DB`` is deliberately evaluated for every call instead of at
    import time.  This keeps tests and separate API server invocations from
    accidentally sharing a database after the environment changes.
    """

    configured_path = os.environ.get("LAZARUS_API_DB")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return (Path.cwd() / "lazarus_api.sqlite3").resolve()


def _require_string(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    return value


def _require_optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name)


def _require_boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean")
    return value


def _require_optional_integer(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer or null")
    return value


def _require_run_id(run_id: Any) -> str:
    return _require_string(run_id, "run_id")


def _ensure_schema(path: Path) -> None:
    """Create and configure the run store once for each absolute DB path."""

    with _INITIALIZATION_LOCK:
        if path in _INITIALIZED_DATABASES and path.exists():
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=10)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            with connection:
                connection.execute(_SCHEMA)

                # This is safe for a newly-created database and lets a development
                # database created before restart recovery existed remain usable.
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(runs)").fetchall()
                }
                if "process_pid" not in columns:
                    connection.execute("ALTER TABLE runs ADD COLUMN process_pid INTEGER")
        finally:
            connection.close()

        _INITIALIZED_DATABASES.add(path)


def _connect() -> sqlite3.Connection:
    path = database_path()
    _ensure_schema(path)
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Yield one transaction-scoped connection and always close it afterward."""

    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _row_to_run(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    result = dict(row)
    for field_name in _BOOLEAN_COLUMNS:
        result[field_name] = bool(result[field_name])
    return result


def _normalise_output_dir(output_dir: str | Path) -> str:
    if isinstance(output_dir, Path):
        candidate = output_dir
    elif isinstance(output_dir, str):
        candidate = Path(_require_string(output_dir, "output_dir"))
    else:
        raise TypeError("output_dir must be a string or pathlib.Path")
    return str(candidate.expanduser().resolve())


def create_run(
    *,
    repo_url: str,
    github_owner: str,
    github_repo: str,
    output_dir: str | Path,
    run_id: str | None = None,
    ref_requested: str | None = None,
    include_closed: bool = False,
    skip_triage: bool = False,
    health_report_only: bool = False,
) -> dict[str, Any]:
    """Persist a newly queued run and return its complete row.

    The normal API path supplies a UUID generated while it reserves that run's
    fresh output directory.  Direct callers can omit ``run_id`` and let this
    function generate a UUID4 hexadecimal identifier instead.
    """

    values = {
        "id": _require_run_id(run_id) if run_id is not None else uuid.uuid4().hex,
        "status": "queued",
        "repo_url": _require_string(repo_url, "repo_url"),
        "github_owner": _require_string(github_owner, "github_owner"),
        "github_repo": _require_string(github_repo, "github_repo"),
        "ref_requested": _require_optional_string(ref_requested, "ref_requested"),
        "include_closed": int(_require_boolean(include_closed, "include_closed")),
        "skip_triage": int(_require_boolean(skip_triage, "skip_triage")),
        "health_report_only": int(
            _require_boolean(health_report_only, "health_report_only")
        ),
        "output_dir": _normalise_output_dir(output_dir),
        "created_at": utc_now(),
    }

    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO runs (
                id, status, repo_url, github_owner, github_repo, ref_requested,
                include_closed, skip_triage, health_report_only, output_dir,
                created_at
            ) VALUES (
                :id, :status, :repo_url, :github_owner, :github_repo, :ref_requested,
                :include_closed, :skip_triage, :health_report_only, :output_dir,
                :created_at
            )
            """,
            values,
        )
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (values["id"],)).fetchone()

    created = _row_to_run(row)
    if created is None:  # pragma: no cover - protects the public contract
        raise RuntimeError("Newly created run could not be read from the database")
    return created


def get_run(run_id: str) -> dict[str, Any] | None:
    """Return one run by id, or ``None`` when it does not exist."""

    identifier = _require_run_id(run_id)
    with _connection() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (identifier,)).fetchone()
    return _row_to_run(row)


def _normalise_update_value(field_name: str, value: Any) -> Any:
    if field_name == "status":
        status = _require_string(value, field_name)
        if status not in RUN_STATUSES:
            allowed = ", ".join(sorted(RUN_STATUSES))
            raise ValueError(f"status must be one of: {allowed}")
        return status

    if field_name in _BOOLEAN_COLUMNS:
        return int(_require_boolean(value, field_name))

    if field_name in {"exit_code", "process_pid"}:
        return _require_optional_integer(value, field_name)

    if field_name == "output_dir":
        return _normalise_output_dir(value)

    if field_name in _NULLABLE_TEXT_COLUMNS:
        return _require_optional_string(value, field_name)

    if field_name in _TEXT_COLUMNS:
        return _require_string(value, field_name)

    raise AssertionError(f"Unexpected update field: {field_name}")


def update_run(run_id: str, **fields: Any) -> dict[str, Any] | None:
    """Update allowed run columns and return the changed row.

    ``None`` means no row matches ``run_id``.  Unknown columns and malformed
    values are rejected before SQLite receives a query.
    """

    identifier = _require_run_id(run_id)
    if not fields:
        raise ValueError("update_run requires at least one field")

    unknown_fields = set(fields) - _UPDATEABLE_COLUMNS
    if unknown_fields:
        rendered = ", ".join(sorted(unknown_fields))
        raise ValueError(f"update_run received unknown field(s): {rendered}")

    normalised_fields = {
        field_name: _normalise_update_value(field_name, value)
        for field_name, value in fields.items()
    }
    assignments = ", ".join(f"{field_name} = ?" for field_name in normalised_fields)
    parameters = [*normalised_fields.values(), identifier]

    with _connection() as connection:
        cursor = connection.execute(
            f"UPDATE runs SET {assignments} WHERE id = ?",  # field names are allowlisted above
            parameters,
        )
        if cursor.rowcount == 0:
            return None
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (identifier,)).fetchone()
    return _row_to_run(row)


def _require_pagination(limit: Any, offset: Any) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise TypeError("offset must be an integer")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if offset < 0:
        raise ValueError("offset must not be negative")
    return limit, offset


def list_runs(*, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Return runs newest first, using a bounded caller-supplied page."""

    page_limit, page_offset = _require_pagination(limit, offset)
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM runs
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (page_limit, page_offset),
        ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]


def list_runs_by_status(
    status: str, *, limit: int = 1000, offset: int = 0
) -> list[dict[str, Any]]:
    """Return runs in one validated status, newest first.

    The background runner uses this at startup to find runs that may need
    best-effort restart recovery.
    """

    validated_status = _require_string(status, "status")
    if validated_status not in RUN_STATUSES:
        allowed = ", ".join(sorted(RUN_STATUSES))
        raise ValueError(f"status must be one of: {allowed}")
    page_limit, page_offset = _require_pagination(limit, offset)

    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM runs
            WHERE status = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (validated_status, page_limit, page_offset),
        ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]
