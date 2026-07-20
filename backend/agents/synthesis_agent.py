"""Synthesize existing Lazarus stage outputs into a maintainer Revival Report.

Directive: ``directives/synthesize_revival_report.md``.

This Layer 2 agent is intentionally read-only.  It does not execute the target
repository, re-run inventory or triage agents, call GitHub, or create a pull
request.  It only verifies and combines the supplied Health Report,
documentation evidence, triage report, and optional draft-PR receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from ._artifact_identity import (
        ArtifactIdentityError,
        identity_from_health_report,
        identity_from_json,
        identity_from_receipt,
        identity_from_triage_report,
        require_same_repository,
    )
except ImportError:  # pragma: no cover - direct CLI execution.
    from _artifact_identity import (
        ArtifactIdentityError,
        identity_from_health_report,
        identity_from_json,
        identity_from_receipt,
        identity_from_triage_report,
        require_same_repository,
    )


DIRECTIVE_PATH = Path("directives/synthesize_revival_report.md")
DOCUMENT_ALLOWLIST = ("README.md", "ARCHITECTURE.md", "CONTRIBUTING.md")
GIT_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?")
GITHUB_PULL_REQUEST_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repository>[A-Za-z0-9_.-]+)/pull/(?P<number>[1-9][0-9]*)/?"
)


class SynthesisBlocked(RuntimeError):
    """Raised when the minimum evidence needed for reliable synthesis is absent."""


def _validate_directive() -> None:
    """Require the directive that governs this non-investigative stage."""
    if not DIRECTIVE_PATH.is_file():
        raise SynthesisBlocked("Synthesis directive is missing: {0}".format(DIRECTIVE_PATH))
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8", errors="replace")
    required_phrases = ("Synthesize Revival Report SOP", "does not re-run inventories", "partial Revival Report")
    missing = [phrase for phrase in required_phrases if phrase not in directive]
    if missing:
        raise SynthesisBlocked("Synthesis directive is incomplete; missing: {0}".format(", ".join(missing)))


def _read_required(path: Path, label: str) -> str:
    """Read a required text artifact, refusing directories and absent files."""
    if not path.is_file():
        raise SynthesisBlocked("Required {0} is missing or not a regular file: {1}".format(label, path))
    return path.read_text(encoding="utf-8", errors="replace")


def _require_heading(text: str, heading: str, label: str) -> None:
    """Ensure a supplied artifact can be traced to its stated producing stage."""
    if not text.startswith(heading + "\n"):
        raise SynthesisBlocked("{0} does not have expected heading {1!r}.".format(label, heading))


def _section(text: str, heading: str) -> str:
    """Extract the body of a Markdown level-two section without interpretation."""
    marker = "## " + heading + "\n"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end < 0 else text[start:end]


def _bullets(section: str) -> list[str]:
    """Return top-level Markdown bullets as plain evidence statements."""
    return [match.group(1).strip() for match in re.finditer(r"(?m)^- (.+)$", section)]


def _sha256(path: Path) -> str:
    """Hash a local documentation candidate for receipt consistency checks."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    """Read a required structured artifact before using its identity declaration."""
    if not path.is_file():
        raise SynthesisBlocked("Required {0} is missing or not a regular file: {1}".format(label, path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SynthesisBlocked("Could not read {0}: {1}".format(label, exc)) from exc
    if not isinstance(payload, dict):
        raise SynthesisBlocked("{0} must be a JSON object: {1}".format(label, path))
    return payload


def _created_draft_pr_receipt_validation(receipt: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Require the complete local evidence shape before claiming a live draft PR.

    A ``draft_pull_request_created`` status string is an assertion inside an
    untrusted local artifact, not sufficient evidence on its own.  We require
    the mode, created commit SHA, GitHub PR URL, and explicit draft flag named
    by the draft-PR contract.  This remains a receipt validation; synthesis
    never performs a fresh GitHub query.
    """
    if not isinstance(receipt, dict):
        return False, ["No JSON draft-PR receipt is available."]

    problems: list[str] = []
    if receipt.get("status") != "draft_pull_request_created":
        problems.append("status is not `draft_pull_request_created`")
    if receipt.get("mode") != "live":
        problems.append("mode is not `live`")

    target = receipt.get("target")
    if not isinstance(target, dict):
        problems.append("target object is missing")
    elif target.get("draft") is not True:
        problems.append("target.draft is not `true`")

    commit_sha = receipt.get("created_commit_sha")
    if not isinstance(commit_sha, str) or GIT_COMMIT_SHA_RE.fullmatch(commit_sha) is None:
        problems.append("created_commit_sha is missing or is not a full Git commit SHA")

    pr_effect: dict[str, Any] | None = None
    effects = receipt.get("remote_side_effects")
    if isinstance(effects, list):
        for effect in effects:
            if isinstance(effect, dict) and isinstance(effect.get("created_draft_pull_request"), str):
                pr_effect = effect
                break
    if pr_effect is None:
        problems.append("remote_side_effects has no created draft-PR URL")
        return False, problems

    pr_url = pr_effect["created_draft_pull_request"]
    url_match = GITHUB_PULL_REQUEST_URL_RE.fullmatch(pr_url)
    if url_match is None:
        problems.append("created draft-PR URL is missing or is not a canonical GitHub pull-request URL")
        return False, problems

    pr_number = pr_effect.get("pull_request_number")
    if not isinstance(pr_number, int) or pr_number != int(url_match.group("number")):
        problems.append("pull_request_number is missing or does not match the created draft-PR URL")

    if isinstance(target, dict):
        owner = target.get("fork_owner")
        repository = target.get("fork_repo")
        if isinstance(owner, str) and isinstance(repository, str):
            if owner.casefold() != url_match.group("owner").casefold() or repository.casefold() != url_match.group("repository").casefold():
                problems.append("created draft-PR URL does not target the receipt's declared fork")
    return not problems, problems


def _parse_receipt(path: Path | None, docs_dir: Path) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Read a PR receipt/preview and report its status without contacting GitHub.

    Returns the parsed receipt, report-status notes, and data-consistency
    warnings.  A preview is valid evidence of a halted safety gate, not proof
    that a pull request exists.
    """
    if path is None:
        return None, ["No draft-PR receipt was supplied; no documentation pull request is evidenced."], []
    if not path.is_file():
        return None, ["Draft-PR receipt is missing: `{0}`; no pull request is evidenced.".format(path)], []
    try:
        # Receipts may be saved by Windows tooling with a UTF-8 BOM.  Accept
        # that standard encoding variant without altering the receipt payload.
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SynthesisBlocked("Draft-PR receipt is not valid JSON: {0}".format(path)) from exc
    if not isinstance(receipt, dict):
        raise SynthesisBlocked("Draft-PR receipt must be a JSON object: {0}".format(path))

    notes: list[str] = []
    warnings: list[str] = []
    mode = receipt.get("mode")
    status = receipt.get("status")
    receipt_proves_live_pr, receipt_shape_problems = _created_draft_pr_receipt_validation(receipt)
    if mode == "preview" and status == "awaiting_operator_approval":
        notes.append("Draft-PR stage halted safely: the supplied receipt is a local preview awaiting explicit operator approval; it records no GitHub API call, branch, commit, or PR.")
    elif receipt_proves_live_pr:
        notes.append("The supplied receipt contains the required local evidence for a created draft pull request; this synthesis does not independently re-check GitHub.")
    else:
        details = "; ".join(receipt_shape_problems) or "receipt shape is incomplete"
        notes.append("Draft-PR receipt status is `{0}` in mode `{1}`, but a created draft PR is not established by this artifact: {2}.".format(status, mode, details))

    candidates = receipt.get("candidate_files")
    if not isinstance(candidates, list):
        warnings.append("Draft-PR receipt has no valid candidate-file list, so its reviewed scope cannot be checked.")
        return receipt, notes, warnings
    by_target = {
        candidate.get("target_path"): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("target_path"), str)
    }
    receipt_targets = set(by_target)
    if receipt_targets != set(DOCUMENT_ALLOWLIST):
        warnings.append(
            "Draft-PR receipt candidate allowlist differs from the required documentation set; receipt lists: {0}.".format(
                ", ".join(sorted(receipt_targets)) or "(none)"
            )
        )
    for filename in DOCUMENT_ALLOWLIST:
        document = docs_dir / filename
        candidate = by_target.get(filename)
        if not document.is_file():
            warnings.append("Current documentation draft is missing `{0}`.".format(filename))
            continue
        expected_hash = candidate.get("sha256") if isinstance(candidate, dict) else None
        actual_hash = _sha256(document)
        if not isinstance(expected_hash, str):
            warnings.append("Receipt lacks a SHA-256 for `{0}`.".format(filename))
        elif actual_hash != expected_hash:
            warnings.append(
                "Receipt hash for `{0}` does not match the current draft (receipt `{1}`, current `{2}`); re-review and refresh the preview before authorizing a PR.".format(
                    filename, expected_hash, actual_hash
                )
            )
    return receipt, notes, warnings


def _triage_counts(triage: str) -> list[str]:
    """Extract the reported category counts without reclassifying any item."""
    counts = _bullets(_section(triage, "Category counts"))
    return counts or ["The triage report did not expose parseable category-count bullets."]


def _pr_summary(triage: str, number: int) -> str:
    """Pull a concise, source-report-only status for a named triaged PR."""
    marker = "### Pull request #{0} -".format(number)
    start = triage.find(marker)
    if start < 0:
        return "PR #{0} is not present in the supplied triage report.".format(number)
    end = triage.find("\n### ", start + len(marker))
    block = triage[start:] if end < 0 else triage[start:end]
    classification = re.search(r"(?m)^- \*\*Classification:\*\* `([^`]+)`", block)
    merge = re.search(r"GitHub merge fields: ([^.]+(?:\.)?)", block)
    recommendation = re.search(r"(?m)^- \*\*Recommended human next step:\*\* (.+)$", block)
    parts = ["PR #{0}".format(number)]
    if classification:
        parts.append("is classified `{0}`".format(classification.group(1)))
    if merge:
        parts.append("with {0}".format(merge.group(1).rstrip(".")))
    result = " ".join(parts) + "."
    if recommendation:
        result += " Triage next step: {0}".format(recommendation.group(1))
    return result


def _health_repository_name(health: str) -> str:
    """Read the diagnosed repository name from the supplied Health Report scope."""
    scope = re.search(r"(?m)^Repository inspected: `([^`]+)`$", _section(health, "Scope"))
    if scope is None:
        raise SynthesisBlocked("Health Report scope does not identify an inspected repository.")
    return re.split(r"[\\/]", scope.group(1).rstrip("\\/"))[-1]


def _semantic_cluster_labels(triage: str) -> list[str]:
    """List reported semantic clusters without reclassifying their individual items."""
    return list(dict.fromkeys(re.findall(r"Semantic cluster `([^`]+)`", triage)))


def _valuable_pr_summaries(triage: str, limit: int = 3) -> list[str]:
    """Surface existing valuable-but-stalled PR assessments from the supplied report."""
    summaries: list[str] = []
    for match in re.finditer(r"(?m)^### Pull request #(\d+) -", triage):
        number = int(match.group(1))
        next_heading = triage.find("\n### ", match.end())
        block = triage[match.start() :] if next_heading < 0 else triage[match.start() : next_heading]
        if "- **Classification:** `valuable-but-stalled-by-inactivity`" in block:
            summaries.append(_pr_summary(triage, number))
        if len(summaries) == limit:
            break
    return summaries


def _artifact_links(health_path: Path, docs_dir: Path, triage_path: Path, receipt_path: Path | None) -> list[str]:
    """Render traceable local artifact paths for a human reviewer."""
    links = [
        "Health Report: `{0}`".format(health_path),
        "Documentation evidence: `{0}`".format(docs_dir / "documentation_evidence.md"),
        "Documentation draft README: `{0}`".format(docs_dir / "README.md"),
        "Triage Report: `{0}`".format(triage_path),
    ]
    if receipt_path is not None:
        links.append("Draft-PR receipt/preview: `{0}`".format(receipt_path))
    return links


def _created_pr_details(receipt: dict[str, Any] | None) -> tuple[str, str | None, str | None]:
    """Describe a receipt-reported created PR without independently querying it."""
    receipt_proves_live_pr, _ = _created_draft_pr_receipt_validation(receipt)
    if not receipt_proves_live_pr or not isinstance(receipt, dict):
        return "The documentation draft exists, but the supplied PR artifact is not complete evidence of a live draft PR.", None, None
    commit_sha = receipt.get("created_commit_sha") if isinstance(receipt.get("created_commit_sha"), str) else None
    pr_url: str | None = None
    pr_number: int | None = None
    effects = receipt.get("remote_side_effects")
    if isinstance(effects, list):
        for effect in effects:
            if isinstance(effect, dict) and isinstance(effect.get("created_draft_pull_request"), str):
                pr_url = effect["created_draft_pull_request"]
                number = effect.get("pull_request_number")
                pr_number = number if isinstance(number, int) else None
                break
    details: list[str] = ["The supplied live receipt reports"]
    if pr_number is not None:
        details.append("draft PR #{0}".format(pr_number))
    else:
        details.append("a draft pull request")
    if pr_url:
        details.append("at `{0}`".format(pr_url))
    if commit_sha:
        details.append("from commit `{0}`".format(commit_sha))
    return " ".join(details) + ". This synthesis does not independently re-check GitHub.", commit_sha, pr_url


def synthesize(
    health_report_path: Path,
    documentation_draft_dir: Path,
    triage_report_path: Path,
    draft_pr_receipt_path: Path | None,
) -> str:
    """Create one evidence-linked Revival Report from existing stage outputs only."""
    _validate_directive()
    health_report_path = health_report_path.resolve()
    documentation_draft_dir = documentation_draft_dir.resolve()
    triage_report_path = triage_report_path.resolve()
    receipt_path = draft_pr_receipt_path.resolve() if draft_pr_receipt_path is not None else None

    health = _read_required(health_report_path, "Health Report")
    triage = _read_required(triage_report_path, "triage report")
    evidence_path = documentation_draft_dir / "documentation_evidence.md"
    evidence = _read_required(evidence_path, "documentation evidence note")
    _require_heading(health, "# Health Report", "Health Report")
    _require_heading(triage, "# Backlog Triage Report", "triage report")
    _require_heading(evidence, "# Documentation Evidence Note", "documentation evidence note")

    inventory_path = documentation_draft_dir / "code_structure_inventory.json"
    documentation_inventory = _read_json_object(inventory_path, "documentation code-structure inventory")

    missing_documents = [filename for filename in DOCUMENT_ALLOWLIST if not (documentation_draft_dir / filename).is_file()]
    if missing_documents:
        raise SynthesisBlocked("Documentation draft is incomplete; missing: {0}".format(", ".join(missing_documents)))

    receipt, pr_notes, consistency_warnings = _parse_receipt(receipt_path, documentation_draft_dir)
    try:
        identities = [
            identity_from_health_report(health, "Health Report"),
            identity_from_json(documentation_inventory, "documentation code-structure inventory"),
            identity_from_triage_report(triage, "triage report"),
        ]
        if receipt is not None:
            identities.append(identity_from_receipt(receipt, "draft-PR receipt"))
        require_same_repository(*identities)
    except ArtifactIdentityError as exc:
        raise SynthesisBlocked(str(exc)) from exc
    receipt_is_live, _ = _created_draft_pr_receipt_validation(receipt)
    partial_reasons = [] if receipt_is_live else list(pr_notes)
    partial_reasons.extend(consistency_warnings)
    status = "Partial Revival Report" if partial_reasons else "Revival Report"

    health_facts = _bullets(_section(health, "Observed Facts"))
    health_inferences = _bullets(_section(health, "Reasonable Inferences"))
    health_blockers = _bullets(_section(health, "Revival Blockers"))
    health_unknowns = _bullets(_section(health, "Unknowns and Limits"))
    documentation_unknowns = _bullets(_section(evidence, "Explicit unknowns"))
    triage_limitations = _bullets(_section(triage, "Limitations"))
    pr_receipt_summary, receipt_commit_sha, receipt_pr_url = _created_pr_details(receipt)
    repository_name = _health_repository_name(health)
    summary_facts = health_facts[:3] or ["The supplied Health Report exposes no parseable observed facts."]
    cluster_labels = _semantic_cluster_labels(triage)
    valuable_pr_summaries = _valuable_pr_summaries(triage)
    documentation_note = (
        "The regenerated README preserves the source-text caveat that `mainpro_FER.py` help strings for `--dataset` and `--bs` appear copy-pasted upstream, rather than treating them as a Lazarus extraction defect."
        if "mainpro_FER.py" in evidence
        else "The documentation evidence keeps static declarations distinct from verified runtime behavior and excludes unverified compatibility claims."
    )

    report_lines = [
        "# {0}: {1}".format(status, repository_name),
        "",
        "## Executive summary",
        "",
        "This is a synthesis of the supplied diagnosis, documentation, triage, and draft-PR artifacts. It does not re-run inventory, fetch current GitHub state, execute repository code, or create a pull request.",
        "",
        "Key observed Health Report facts: " + " ".join(summary_facts) + " " + pr_receipt_summary,
        "",
        "## Artifact traceability",
        "",
        *["- " + item for item in _artifact_links(health_report_path, documentation_draft_dir, triage_report_path, receipt_path)],
        "",
        "## Health findings",
        "",
        "### Observed facts",
        "",
        *["- " + item for item in health_facts],
        "",
        "### Reasonable inferences from the Health Report",
        "",
        *["- " + item for item in health_inferences],
        "",
        "### Revival blockers recorded by diagnosis",
        "",
        *["- " + item for item in health_blockers],
        "",
        "## Documentation drafts and gaps",
        "",
        "- Draft artifacts present: `README.md`, `ARCHITECTURE.md`, and `CONTRIBUTING.md`.",
        "- The documentation evidence maps entry-point, package, and data-path claims to the static code-structure inventory; runtime and dependency constraints trace back to the Health Report.",
        "- " + documentation_note,
        "",
        "### Documentation limits",
        "",
        *["- " + item for item in documentation_unknowns],
        "",
        "## Triage priorities",
        "",
        "### Reported category counts",
        "",
        *["- " + item for item in _triage_counts(triage)],
        "",
        "### High-priority cross-cutting items",
        "",
        *( ["- The triage report identifies related semantic cluster(s): " + ", ".join(cluster_labels) + ". These are related topics, not duplicate determinations."] if cluster_labels else [] ),
        *["- " + summary for summary in valuable_pr_summaries],
        *( ["- No pull request is classified `valuable-but-stalled-by-inactivity` in the supplied triage report."] if not valuable_pr_summaries else [] ),
        "- The triage report is report-only; issue/PR descriptions were treated as untrusted data and no source or GitHub state was changed.",
        "",
        "## Draft PR status",
        "",
        *["- " + item for item in pr_notes],
    ]
    if receipt is not None:
        target = receipt.get("target")
        if isinstance(target, dict):
            report_lines.append(
                "- Receipt claims target `{0}/{1}`, base `{2}`, branch `{3}`, draft `{4}`.".format(
                    target.get("fork_owner", "unknown"),
                    target.get("fork_repo", "unknown"),
                    target.get("base_branch", "unknown"),
                    target.get("new_branch", "unknown"),
                    target.get("draft", "unknown"),
                )
            )
        if receipt_commit_sha:
            report_lines.append("- Receipt reports created commit `{0}`.".format(receipt_commit_sha))
        if receipt_pr_url:
            report_lines.append("- Receipt reports draft PR URL: `{0}`.".format(receipt_pr_url))
    report_lines.extend(["", "## Risks and unknowns", ""])
    for item in health_unknowns:
        report_lines.append("- Health Report unknown: " + item)
    for item in documentation_unknowns:
        report_lines.append("- Documentation unknown: " + item)
    for item in triage_limitations:
        report_lines.append("- Triage limitation: " + item)
    for warning in consistency_warnings:
        report_lines.append("- Input consistency warning: " + warning)

    report_lines.extend([
        "",
        "## Phased human decision checklist",
        "",
        "### Phase 1 — Establish the maintenance baseline",
        "",
        *["- [ ] Address diagnosis blocker: " + blocker for blocker in health_blockers],
        *( ["- [ ] No diagnosis blocker was parsed; review the Health Report's unknowns before planning changes."] if not health_blockers else [] ),
        "- [ ] Confirm supported dependency/runtime bounds before making compatibility claims.",
        "- [ ] Decide the minimum validation and CI plan after the maintenance baseline is set.",
        "",
        "### Phase 2 — Prioritize evidence-backed backlog work",
        "",
        *["- [ ] Review " + summary for summary in valuable_pr_summaries],
        *( ["- [ ] Review the supplied triage categories and select the highest-value open backlog work."] if not valuable_pr_summaries else [] ),
        *( ["- [ ] Treat semantic cluster(s) " + ", ".join(cluster_labels) + " as coordinated review topics; do not infer duplicates or resolution from clustering alone."] if cluster_labels else [] ),
        "",
        "### Phase 3 — Review and authorize the documentation PR",
        "",
        "- [ ] Review the three allowlisted documentation files and their evidence mapping.",
        "- [ ] Refresh the draft-PR preview/receipt after any reviewed documentation change, so candidate hashes match the reviewed files.",
        "- [ ] Review the existing draft PR without merging it automatically." if receipt_is_live else "- [ ] If the operator wants a PR, explicitly authorize the draft-only creation on the confirmed operator-owned fork; do not merge it automatically.",
        "",
        "## Synthesis status",
        "",
    ])
    if partial_reasons:
        report_lines.extend([
            "This is a **partial Revival Report**. The available diagnosis, documentation, and triage stages are preserved. The draft-PR stage has not produced a complete, current evidence-backed created-PR receipt, so downstream review of a live draft PR remains pending.",
            "",
            "### Missing or halted stage details",
            "",
            *["- " + reason for reason in partial_reasons],
            "",
            "### Exact human decision needed to resume",
            "",
            "- Re-review the current documentation hashes, refresh the preview, and provide explicit approval to create one draft documentation PR on the confirmed operator-owned fork."
        ])
    else:
        report_lines.append("All supplied stage artifacts passed the synthesis consistency checks. This report still does not independently verify remote PR state.")
    return "\n".join(report_lines).rstrip() + "\n"


def _write_blocked_receipt(output_path: Path | None, error: str) -> None:
    """Emit the directive-required blocked receipt when reliable synthesis is impossible."""
    receipt = {"status": "blocked", "stage": "synthesize", "reason": error}
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    sys.stderr.write(rendered)


def main(argv: list[str] | None = None) -> int:
    """Read the four supplied artifacts and emit a pure-synthesis Revival Report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-report", type=Path, required=True, help="Existing Health Report from diagnosis_agent.py.")
    parser.add_argument("--documentation-draft-dir", type=Path, required=True, help="Existing documentation draft directory.")
    parser.add_argument("--triage-report", type=Path, required=True, help="Existing full triage report.")
    parser.add_argument("--draft-pr-receipt", type=Path, help="Optional existing draft-PR receipt or preview JSON.")
    parser.add_argument("--output", type=Path, required=True, help="Revival Report destination outside the target clone.")
    args = parser.parse_args(argv)
    try:
        report = synthesize(
            args.health_report,
            args.documentation_draft_dir,
            args.triage_report,
            args.draft_pr_receipt,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        sys.stdout.write(json.dumps({"status": "written", "report": str(args.output.resolve())}, indent=2) + "\n")
        return 0
    except SynthesisBlocked as exc:
        _write_blocked_receipt(args.output, str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
