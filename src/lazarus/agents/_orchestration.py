"""Shared, side-effect-free helpers for Lazarus Layer 2 agents.

These helpers mirror the LedgerGuard orchestration pattern: agents read their
current directive and invoke one deterministic Layer 3 script without a shell.
Safety policy and external mutations remain in the execution script itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DIRECTIVES_DIR = PACKAGE_ROOT / "directives"
EXECUTION_DIR = PACKAGE_ROOT / "execution"


def read_directive(filename: str) -> str:
    """Read a trusted Layer 1 directive at runtime for the current SOP."""
    if Path(filename).name != filename:
        raise ValueError("Directive name must be a single filename.")
    path = DIRECTIVES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError("Directive is missing: {0}".format(path))
    return path.read_text(encoding="utf-8")


def call_execution(script_name: str, arguments: Sequence[str]) -> dict[str, Any]:
    """Call a Layer 3 script without a shell and return its JSON-object output."""
    if Path(script_name).name != script_name:
        raise ValueError("Execution script name must be a single filename.")
    script_path = EXECUTION_DIR / script_name
    if not script_path.is_file():
        raise FileNotFoundError("Execution script is missing: {0}".format(script_path))

    completed = subprocess.run(
        [sys.executable, str(script_path), *arguments],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "No output returned."
        raise RuntimeError("Execution script failed: {0}: {1}".format(script_name, detail))
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Execution script returned invalid JSON: {0}".format(script_name)) from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Execution script returned a non-object payload: {0}".format(script_name))
    return dict(payload)
