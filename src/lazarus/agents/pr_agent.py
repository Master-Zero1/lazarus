"""Orchestrate the documentation draft-PR SOP without duplicating Layer 3 safety.

Directive: ``directives/draft_pr.md``.  This Layer 2 agent reads the SOP and
passes caller-selected inputs to ``execution/open_draft_pr.py``.  The
execution script alone owns fork verification, documentation allowlisting,
branch/PR collision checks, GitHub interaction, and all remote mutations.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ._orchestration import call_execution, read_directive


DIRECTIVE = "draft_pr.md"
EXECUTION_SCRIPT = "open_draft_pr.py"
Executor = Callable[[str, Sequence[str]], dict[str, Any]]


def _validate_directive() -> None:
    """Confirm the current SOP still delegates the PR operation to Layer 3."""
    directive = read_directive(DIRECTIVE)
    if EXECUTION_SCRIPT not in directive:
        raise RuntimeError("Draft PR SOP does not name {0}.".format(EXECUTION_SCRIPT))
    for required_input in ("expected_upstream", "health_report", "docs_evidence_path"):
        if required_input not in directive:
            raise RuntimeError("Draft PR SOP does not name required input {0}.".format(required_input))


def request_draft_pr(
    docs_draft_dir: Path,
    fork_owner: str,
    fork_repo: str,
    *,
    expected_upstream: str,
    health_report: Path,
    docs_evidence_path: Path,
    base: str = "master",
    branch: str | None = None,
    title: str | None = None,
    operator_approval: str | None = None,
    execute: bool = False,
    output: Path | None = None,
    executor: Executor = call_execution,
) -> dict[str, Any]:
    """Request a Layer 3 PR preview, or an explicitly caller-approved live attempt.

    In live mode, this agent requires a non-empty approval argument supplied by
    its caller and forwards it unchanged.  It deliberately does not interpret
    approval wording or replicate any execution-layer safety check.  Preview
    mode passes no approval and cannot create a remote resource.
    """
    _validate_directive()
    if execute and (not isinstance(operator_approval, str) or not operator_approval.strip()):
        raise ValueError("Live draft-PR requests require an explicit operator_approval supplied by the caller.")

    arguments = [
        str(docs_draft_dir),
        "--fork-owner",
        fork_owner,
        "--fork-repo",
        fork_repo,
        "--expected-upstream",
        expected_upstream,
        "--health-report",
        str(health_report),
        "--docs-evidence-path",
        str(docs_evidence_path),
        "--base",
        base,
    ]
    if branch is not None:
        arguments.extend(["--branch", branch])
    if title is not None:
        arguments.extend(["--title", title])
    if output is not None:
        arguments.extend(["--output", str(output)])
    if execute:
        arguments.extend(["--execute", "--operator-approval", operator_approval])
    return executor(EXECUTION_SCRIPT, arguments)


def main(argv: list[str] | None = None) -> int:
    """Run the Draft PR orchestrator; default safely to a local preview."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docs_draft_dir", type=Path, help="Reviewed documentation-draft directory.")
    parser.add_argument("--fork-owner", required=True, help="Operator-owned fork owner.")
    parser.add_argument("--fork-repo", required=True, help="Operator-owned fork repository.")
    parser.add_argument("--expected-upstream", required=True, help="Expected parent repository as owner/name.")
    parser.add_argument("--health-report", type=Path, required=True, help="Target repository Health Report for PR-body evidence.")
    parser.add_argument("--docs-evidence-path", type=Path, required=True, help="Documentation claim-to-source mapping generated with the reviewed draft.")
    parser.add_argument("--base", default="master", help="Target base branch.")
    parser.add_argument("--branch", help="Optional new documentation branch name.")
    parser.add_argument("--title", help="Optional draft PR title.")
    parser.add_argument("--operator-approval", help="Caller-supplied approval required only with --execute.")
    parser.add_argument("--execute", action="store_true", help="Request a live Layer 3 attempt; omitted means preview.")
    parser.add_argument("--output", type=Path, help="Optional local Layer 3 receipt path.")
    args = parser.parse_args(argv)
    try:
        result = request_draft_pr(
            args.docs_draft_dir,
            args.fork_owner,
            args.fork_repo,
            expected_upstream=args.expected_upstream,
            health_report=args.health_report,
            docs_evidence_path=args.docs_evidence_path,
            base=args.base,
            branch=args.branch,
            title=args.title,
            operator_approval=args.operator_approval,
            execute=args.execute,
            output=args.output,
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
