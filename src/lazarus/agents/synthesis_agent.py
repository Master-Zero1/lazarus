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

from ._artifact_identity import (
    ArtifactIdentityError,
    identity_from_health_report,
    identity_from_json,
    identity_from_receipt,
    identity_from_triage_report,
    require_same_repository,
)


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DIRECTIVE_PATH = PACKAGE_ROOT / "directives" / "synthesize_revival_report.md"
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


def _awaiting_approval_preview_validation(receipt: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Recognize a complete, deliberately non-writing draft-PR preview.

    A preview that is explicitly awaiting operator approval is not a failed
    stage: it is the expected outcome when no write was authorized.  It still
    cannot prove that a GitHub pull request exists, so require enough receipt
    structure to distinguish it from a malformed or halted artifact before it
    can leave an otherwise complete Revival Report at full status.
    """
    if not isinstance(receipt, dict):
        return False, ["No JSON draft-PR preview receipt is available."]

    problems: list[str] = []
    if receipt.get("mode") != "preview":
        problems.append("mode is not `preview`")
    if receipt.get("status") != "awaiting_operator_approval":
        problems.append("status is not `awaiting_operator_approval`")
    if not isinstance(receipt.get("approval_required"), str) or not receipt["approval_required"].strip():
        problems.append("approval_required is missing")

    target = receipt.get("target")
    if not isinstance(target, dict):
        problems.append("target object is missing")
    elif target.get("draft") is not True:
        problems.append("target.draft is not `true`")

    candidates = receipt.get("candidate_files")
    if not isinstance(candidates, list):
        problems.append("candidate_files is not a list")

    remote_effects = receipt.get("remote_side_effects")
    if not isinstance(remote_effects, str) or "does not contact github" not in remote_effects.casefold():
        problems.append("remote_side_effects does not establish the preview's no-write boundary")
    return not problems, problems


def _parse_receipt(path: Path | None, docs_dir: Path | None) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Read a PR receipt/preview and report its status without contacting GitHub.

    Returns the parsed receipt, report-status notes, and data-consistency
    warnings.  A complete preview records a pending authorization boundary,
    not a failed stage and not proof that a pull request exists.
    """
    if path is None:
        return None, ["No draft-PR receipt was supplied; no documentation pull request is evidenced."], []
    if not path.is_file():
        return None, ["Draft-PR receipt is missing: `{0}`; no pull request is evidenced.".format(path)], []
    try:
        # Receipts may be saved by Windows tooling with a UTF-8 BOM.  Accept
        # that standard encoding variant without altering the receipt payload.
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise SynthesisBlocked("Draft-PR receipt could not be read: {0}: {1}".format(path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise SynthesisBlocked("Draft-PR receipt is not valid JSON: {0}".format(path)) from exc
    if not isinstance(receipt, dict):
        raise SynthesisBlocked("Draft-PR receipt must be a JSON object: {0}".format(path))

    notes: list[str] = []
    warnings: list[str] = []
    mode = receipt.get("mode")
    status = receipt.get("status")
    receipt_proves_live_pr, receipt_shape_problems = _created_draft_pr_receipt_validation(receipt)
    receipt_is_pending_preview, preview_shape_problems = _awaiting_approval_preview_validation(receipt)
    if receipt_is_pending_preview:
        notes.append("Draft-PR preview was generated safely and awaits explicit operator approval; it records no GitHub API call, branch, commit, or pull request.")
    elif receipt_proves_live_pr:
        notes.append("The supplied receipt contains the required local evidence for a created draft pull request; this synthesis does not independently re-check GitHub.")
    else:
        details = "; ".join(receipt_shape_problems + preview_shape_problems) or "receipt shape is incomplete"
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
    if docs_dir is None:
        warnings.append("Current documentation draft is unavailable, so receipt candidate hashes cannot be checked.")
        return receipt, notes, warnings
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


def _artifact_links(
    health_path: Path | None,
    docs_dir: Path | None,
    triage_path: Path | None,
    receipt_path: Path | None,
) -> list[str]:
    """Render traceable artifact labels without leaking host-specific paths."""
    links: list[str] = []
    if health_path is not None:
        links.append("Health Report: `{0}` (supplied local artifact)".format(health_path.name))
    if docs_dir is not None:
        links.extend(
            [
                "Documentation evidence: `docs_draft/documentation_evidence.md` (supplied local artifact)",
                "Documentation draft README: `docs_draft/README.md` (supplied local artifact)",
            ]
        )
    if triage_path is not None:
        links.append("Triage Report: `{0}` (supplied local artifact)".format(triage_path.name))
    if receipt_path is not None:
        links.append("Draft-PR receipt/preview: `{0}` (supplied local artifact)".format(receipt_path.name))
    return links


def _created_pr_details(receipt: dict[str, Any] | None) -> tuple[str, str | None, str | None]:
    """Describe a receipt-reported created PR without independently querying it."""
    receipt_proves_live_pr, _ = _created_draft_pr_receipt_validation(receipt)
    receipt_is_pending_preview, _ = _awaiting_approval_preview_validation(receipt)
    if receipt_is_pending_preview:
        return (
            "The supplied draft-PR preview awaits explicit operator approval; no GitHub write or live pull request is evidenced.",
            None,
            None,
        )
    if not receipt_proves_live_pr or not isinstance(receipt, dict):
        return "The supplied PR artifact is not complete evidence of a live draft PR.", None, None
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


def _load_optional_health_report(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """Load one Health Report independently so absent/invalid evidence can yield a partial report."""
    if path is None:
        return None, "Health Report was not supplied."
    resolved = path.resolve()
    try:
        content = _read_required(resolved, "Health Report")
        _require_heading(content, "# Health Report", "Health Report")
        return {"path": resolved, "content": content, "identity": identity_from_health_report(content, "Health Report")}, None
    except (OSError, SynthesisBlocked, ArtifactIdentityError, ValueError) as exc:
        return None, "Health Report was supplied but could not be used: {0}".format(exc)


def _load_optional_documentation_draft(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """Load the complete documentation bundle independently of the other two core stages."""
    if path is None:
        return None, "Documentation draft was not supplied."
    resolved = path.resolve()
    try:
        if not resolved.is_dir():
            raise SynthesisBlocked("Documentation draft directory is missing or not a directory: {0}".format(resolved))
        evidence_path = resolved / "documentation_evidence.md"
        evidence = _read_required(evidence_path, "documentation evidence note")
        _require_heading(evidence, "# Documentation Evidence Note", "documentation evidence note")
        inventory_path = resolved / "code_structure_inventory.json"
        inventory = _read_json_object(inventory_path, "documentation code-structure inventory")
        missing_documents = [filename for filename in DOCUMENT_ALLOWLIST if not (resolved / filename).is_file()]
        if missing_documents:
            raise SynthesisBlocked("Documentation draft is incomplete; missing: {0}".format(", ".join(missing_documents)))
        return {
            "path": resolved,
            "evidence": evidence,
            "inventory": inventory,
            "identity": identity_from_json(inventory, "documentation code-structure inventory"),
        }, None
    except (OSError, SynthesisBlocked, ArtifactIdentityError, ValueError) as exc:
        return None, "Documentation draft was supplied but could not be used: {0}".format(exc)


def _load_optional_triage_report(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """Load one triage report independently so other valid stages remain reportable."""
    if path is None:
        return None, "Triage Report was not supplied."
    resolved = path.resolve()
    try:
        content = _read_required(resolved, "triage report")
        _require_heading(content, "# Backlog Triage Report", "triage report")
        return {"path": resolved, "content": content, "identity": identity_from_triage_report(content, "triage report")}, None
    except (OSError, SynthesisBlocked, ArtifactIdentityError, ValueError) as exc:
        return None, "Triage Report was supplied but could not be used: {0}".format(exc)


def synthesize(
    health_report_path: Path | None,
    documentation_draft_dir: Path | None,
    triage_report_path: Path | None,
    draft_pr_receipt_path: Path | None,
) -> str:
    """Create one evidence-linked Revival Report from existing stage outputs only."""
    _validate_directive()
    receipt_path = draft_pr_receipt_path.resolve() if draft_pr_receipt_path is not None else None
    health_data, health_problem = _load_optional_health_report(health_report_path)
    docs_data, docs_problem = _load_optional_documentation_draft(documentation_draft_dir)
    triage_data, triage_problem = _load_optional_triage_report(triage_report_path)
    if health_data is not None and docs_data is not None and health_data["identity"].upstream is not None:
        try:
            docs_data["identity"] = identity_from_json(
                docs_data["inventory"],
                "documentation code-structure inventory",
                expected_upstream=health_data["identity"].upstream,
            )
        except ArtifactIdentityError as exc:
            raise SynthesisBlocked(
                "Repository identity validation rejected the supplied documentation draft: {0}".format(exc)
            ) from exc
    core_problems = [problem for problem in (health_problem, docs_problem, triage_problem) if problem]
    usable_core = [data for data in (health_data, docs_data, triage_data) if data is not None]

    # One fully valid core artifact is the minimum reliable bar: it gives this
    # read-only stage source-backed material to preserve without inventing the
    # absent stages. With none, even a partial report would be speculative.
    if not usable_core:
        raise SynthesisBlocked("No usable core stage artifact is available: {0}".format("; ".join(core_problems)))

    try:
        repository_identity = require_same_repository(*(data["identity"] for data in usable_core))
    except ArtifactIdentityError as exc:
        # Valid artifacts that disagree must halt rather than have one silently
        # discarded; this preserves the established cross-repository binding.
        raise SynthesisBlocked("Repository identity validation rejected supplied core artifacts: {0}".format(exc)) from exc

    try:
        receipt, pr_notes, consistency_warnings = _parse_receipt(
            receipt_path, docs_data["path"] if docs_data is not None else None
        )
    except (OSError, SynthesisBlocked, ValueError) as exc:
        receipt = None
        pr_notes = ["Draft-PR receipt was supplied but could not be parsed or validated: {0}".format(exc)]
        consistency_warnings = []

    if receipt is not None:
        try:
            receipt_identity = identity_from_receipt(receipt, "draft-PR receipt")
        except ArtifactIdentityError as exc:
            receipt = None
            pr_notes = ["Draft-PR receipt was supplied but failed identity validation: {0}".format(exc)]
            consistency_warnings = []
        else:
            if receipt_identity is not None:
                try:
                    require_same_repository(repository_identity, receipt_identity)
                except ArtifactIdentityError as exc:
                    raise SynthesisBlocked(
                        "Repository identity validation rejected the supplied draft-PR receipt: {0}".format(exc)
                    ) from exc

    receipt_is_live, _ = _created_draft_pr_receipt_validation(receipt)
    receipt_is_pending_preview, _ = _awaiting_approval_preview_validation(receipt)
    receipt_partial_reasons = []
    if receipt_path is not None and not receipt_is_live and not receipt_is_pending_preview:
        receipt_partial_reasons.extend(pr_notes)
    partial_reasons = [*core_problems, *receipt_partial_reasons, *consistency_warnings]
    status = "Partial Revival Report" if partial_reasons else "Revival Report"

    health = health_data["content"] if health_data is not None else ""
    evidence = docs_data["evidence"] if docs_data is not None else ""
    triage = triage_data["content"] if triage_data is not None else ""
    health_facts = _bullets(_section(health, "Observed Facts")) if health else []
    health_inferences = _bullets(_section(health, "Reasonable Inferences")) if health else []
    health_blockers = _bullets(_section(health, "Revival Blockers")) if health else []
    health_unknowns = _bullets(_section(health, "Unknowns and Limits")) if health else []
    documentation_unknowns = _bullets(_section(evidence, "Explicit unknowns")) if evidence else []
    triage_limitations = _bullets(_section(triage, "Limitations")) if triage else []
    if receipt_path is None:
        pr_receipt_summary = "No draft-PR receipt was supplied, so this synthesis represents no requested or authorized PR attempt."
        receipt_commit_sha = None
        receipt_pr_url = None
    else:
        pr_receipt_summary, receipt_commit_sha, receipt_pr_url = _created_pr_details(receipt)
    repository_name = _health_repository_name(health) if health else repository_identity.name
    summary_facts = health_facts[:3] or ["No usable Health Report facts were supplied."]
    cluster_labels = _semantic_cluster_labels(triage) if triage else []
    valuable_pr_summaries = _valuable_pr_summaries(triage) if triage else []
    documentation_note = (
        "The regenerated README preserves the source-text caveat that `mainpro_FER.py` help strings for `--dataset` and `--bs` appear copy-pasted upstream, rather than treating them as a Lazarus extraction defect."
        if "mainpro_FER.py" in evidence
        else "The documentation evidence keeps static declarations distinct from verified runtime behavior and excludes unverified compatibility claims."
    )
    available_stages = [
        label
        for label, data in (("diagnosis", health_data), ("documentation", docs_data), ("triage", triage_data))
        if data is not None
    ]

    report_lines = [
        "# {0}: {1}".format(status, repository_name),
        "",
        "## Executive summary",
        "",
        "This is a synthesis of the supplied stage artifacts. It does not re-run inventory, fetch current GitHub state, execute repository code, or create a pull request.",
        "",
        "Available core stages: " + ", ".join(available_stages) + ".",
        "Key observed Health Report facts: " + " ".join(summary_facts) + " " + pr_receipt_summary,
        "",
        "## Artifact traceability",
        "",
        *[
            "- " + item
            for item in _artifact_links(
                health_data["path"] if health_data is not None else None,
                docs_data["path"] if docs_data is not None else None,
                triage_data["path"] if triage_data is not None else None,
                receipt_path if receipt is not None else None,
            )
        ],
        "",
        "## Health findings",
        "",
        "### Observed facts",
        "",
        *(["- " + item for item in health_facts] if health_data is not None else ["- No usable Health Report is available; no diagnosis facts are included."]),
        "",
        "### Reasonable inferences from the Health Report",
        "",
        *(["- " + item for item in health_inferences] if health_data is not None else ["- No usable Health Report is available; no diagnosis inferences are included."]),
        "",
        "### Revival blockers recorded by diagnosis",
        "",
        *(["- " + item for item in health_blockers] if health_data is not None else ["- No usable Health Report is available; no diagnosis blockers are included."]),
        "",
        "## Documentation drafts and gaps",
        "",
        *(
            [
                "- Draft artifacts present: `README.md`, `ARCHITECTURE.md`, and `CONTRIBUTING.md`.",
                "- The documentation evidence maps entry-point, package, and data-path claims to the static code-structure inventory; runtime and dependency constraints trace back to a supplied Health Report when one is available.",
                "- " + documentation_note,
            ]
            if docs_data is not None
            else ["- No usable documentation draft is available; no regenerated-documentation claims are included."]
        ),
        "",
        "### Documentation limits",
        "",
        *(
            ["- " + item for item in documentation_unknowns]
            if docs_data is not None
            else ["- Documentation-stage limits cannot be extracted because no usable documentation evidence note is available."]
        ),
        "",
        "## Triage priorities",
        "",
        "### Reported category counts",
        "",
        *(
            ["- " + item for item in _triage_counts(triage)]
            if triage_data is not None
            else ["- No usable triage report is available; no backlog category counts are included."]
        ),
        "",
        "### High-priority cross-cutting items",
        "",
        *(
            [
                *(
                    [
                        "- The triage report identifies related semantic cluster(s): "
                        + ", ".join(cluster_labels)
                        + ". These are related topics, not duplicate determinations."
                    ]
                    if cluster_labels
                    else []
                ),
                *["- " + summary for summary in valuable_pr_summaries],
                *(
                    ["- No pull request is classified `valuable-but-stalled-by-inactivity` in the supplied triage report."]
                    if not valuable_pr_summaries
                    else []
                ),
                "- The triage report is report-only; issue/PR descriptions were treated as untrusted data and no source or GitHub state was changed.",
            ]
            if triage_data is not None
            else ["- No usable triage report is available; no backlog priority is inferred."]
        ),
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
    for problem in core_problems:
        report_lines.append("- Missing or invalid stage artifact: " + problem)
    for warning in consistency_warnings:
        report_lines.append("- Input consistency warning: " + warning)

    report_lines.extend(["", "## Phased human decision checklist", "", "### Phase 1 — Establish the maintenance baseline", ""])
    if health_data is None:
        report_lines.append("- [ ] Supply or regenerate a valid Health Report before making diagnosis-backed maintenance decisions.")
    else:
        report_lines.extend("- [ ] Address diagnosis blocker: " + blocker for blocker in health_blockers)
        if not health_blockers:
            report_lines.append("- [ ] No diagnosis blocker was parsed; review the Health Report's unknowns before planning changes.")
        report_lines.append("- [ ] Confirm supported dependency/runtime bounds before making compatibility claims.")
        report_lines.append("- [ ] Decide the minimum validation and CI plan after the maintenance baseline is set.")

    report_lines.extend(["", "### Phase 2 — Prioritize evidence-backed backlog work", ""])
    if triage_data is None:
        report_lines.append("- [ ] Supply or regenerate a valid Triage Report before prioritizing backlog work.")
    else:
        report_lines.extend("- [ ] Review " + summary for summary in valuable_pr_summaries)
        if not valuable_pr_summaries:
            report_lines.append("- [ ] Review the supplied triage categories and select the highest-value open backlog work.")
        if cluster_labels:
            report_lines.append(
                "- [ ] Treat semantic cluster(s) {0} as coordinated review topics; do not infer duplicates or resolution from clustering alone.".format(
                    ", ".join(cluster_labels)
                )
            )

    report_lines.extend(["", "### Phase 3 — Review and authorize the documentation PR", ""])
    if docs_data is None:
        report_lines.append("- [ ] Supply or regenerate the reviewed documentation draft and evidence note before considering a documentation PR.")
    else:
        report_lines.append("- [ ] Review the three allowlisted documentation files and their evidence mapping.")
        report_lines.append("- [ ] Refresh the draft-PR preview/receipt after any reviewed documentation change, so candidate hashes match the reviewed files.")
    report_lines.append(
        "- [ ] Review the existing draft PR without merging it automatically."
        if receipt_is_live
        else "- [ ] If the operator wants a PR, explicitly authorize the draft-only creation on the confirmed operator-owned fork; do not merge it automatically."
    )
    report_lines.extend(["", "## Synthesis status", ""])
    if partial_reasons:
        report_lines.extend([
            "This is a **partial Revival Report**. Only the usable core stages listed above were preserved; absent or invalid artifacts were not replaced with new investigation.",
            "",
            "### Missing or halted stage details",
            "",
            *["- " + reason for reason in partial_reasons],
            "",
            "### Exact human decision needed to resume",
            "",
            "- Decide whether to supply or regenerate each named missing or invalid core artifact before treating this report as complete.",
        ])
        if receipt_path is not None and not receipt_is_live and not receipt_is_pending_preview:
            report_lines.append("- If a documentation PR was intended, resolve the receipt issue, refresh the reviewed preview if needed, and provide explicit draft-only approval on the confirmed fork.")
    else:
        report_lines.append("All supplied stage artifacts passed the synthesis consistency checks. This report still does not independently verify remote PR state.")
    return "\n".join(report_lines).rstrip() + "\n"


def _write_blocked_receipt(output_path: Path | None, error: str) -> None:
    """Emit the directive-required blocked receipt when reliable synthesis is impossible.

    A report-output failure must not turn the blocked receipt itself into an
    unhandled traceback.  We attempt the requested destination once, then
    preserve the structured receipt on stderr if that persistence fails.
    """
    receipt = {"status": "blocked", "stage": "synthesize", "reason": error}
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if output_path is not None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            receipt["reason"] = "{0}; additionally, the blocked receipt could not be written to {1}: {2}".format(
                error, output_path, exc
            )
            rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    sys.stderr.write(rendered)


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_output_path(
    output_path: Path,
    health_report_path: Path | None,
    documentation_draft_dir: Path | None,
    triage_report_path: Path | None,
    draft_pr_receipt_path: Path | None,
) -> None:
    """Reject a Revival Report destination that overlaps any supplied evidence."""
    if documentation_draft_dir is not None and _is_within(output_path, documentation_draft_dir):
        raise ValueError(
            "Output path {0} is inside documentation draft directory {1}.".format(
                output_path.resolve(), documentation_draft_dir.resolve()
            )
        )
    inputs: list[tuple[str, Path]] = []
    if health_report_path is not None:
        inputs.append(("Health Report", health_report_path))
    if triage_report_path is not None:
        inputs.append(("triage report", triage_report_path))
    if draft_pr_receipt_path is not None:
        inputs.append(("draft-PR receipt", draft_pr_receipt_path))
    for label, input_path in inputs:
        if output_path.resolve() == input_path.resolve():
            raise ValueError(
                "Output path {0} collides with input {1} {2}.".format(output_path.resolve(), label, input_path.resolve())
            )


def main(argv: list[str] | None = None) -> int:
    """Read the four supplied artifacts and emit a pure-synthesis Revival Report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-report", type=Path, help="Optional existing Health Report from diagnosis_agent.py.")
    parser.add_argument("--documentation-draft-dir", type=Path, help="Optional existing documentation draft directory.")
    parser.add_argument("--triage-report", type=Path, help="Optional existing full triage report.")
    parser.add_argument("--draft-pr-receipt", type=Path, help="Optional existing draft-PR receipt or preview JSON.")
    parser.add_argument("--output", type=Path, required=True, help="Revival Report destination outside the target clone.")
    args = parser.parse_args(argv)
    try:
        _validate_output_path(
            args.output,
            args.health_report,
            args.documentation_draft_dir,
            args.triage_report,
            args.draft_pr_receipt,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
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
    except OSError as exc:
        _write_blocked_receipt(
            args.output,
            "I/O failure while reading or writing synthesis artifacts: {0}".format(exc),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
