"""Produce a non-destructive issue and pull-request triage report.

This is Layer 2 orchestration for ``directives/triage_issues_and_prs.md``. It
reads supplied, read-only GitHub snapshots and makes review recommendations; it
does not execute repository content or call a write-capable GitHub endpoint.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from ._artifact_identity import identity_from_health_report, identity_from_json, require_same_repository
except ImportError:  # pragma: no cover - direct CLI execution.
    from _artifact_identity import identity_from_health_report, identity_from_json, require_same_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIRECTIVE_PATH = REPOSITORY_ROOT / "directives" / "triage_issues_and_prs.md"
CATEGORIES = (
    "duplicate/resolved",
    "obsolete",
    "still valid",
    "valuable-but-stalled-by-inactivity",
)
CHANGE_INTENT_RE = re.compile(
    r"\b(add|correct(?:ed|ion)?|dump|fix|implement|improve|rename|scrap(?:e|ing)|split|support|update|compatib(?:ility|le)|feature)\b",
    re.IGNORECASE,
)
# A state transition and a textual resolution claim are different evidence.
# Keep this deliberately narrow: generic phrases such as "duplicate of" can
# describe a domain relationship (for example, a duplicate game record) rather
# than an issue tracker relationship.  A matched signal must identify an issue
# or pull request, or make an unambiguous claim that this issue is resolved.
TEXTUAL_RESOLUTION_RE = re.compile(
    r"\b(?:"
    r"(?:this\s+)?issue\s+(?:has\s+been\s+|is\s+)?(?:fixed|resolved)"
    r"|already\s+(?:fixed|resolved)"
    r"|(?:fixed|resolved)\s+(?:in|by)\s+(?:issue|pr|pull\s+request)\s*#\d+"
    r"|duplicate\s+of\s+(?:issue\s+)?#\d+"
    r")\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[a-z0-9_+.-]{3,}", re.IGNORECASE)
STOPWORDS = {"the", "and", "that", "this", "with", "from", "have", "does", "when", "what", "where", "there", "please", "about", "code", "python", "mainpro", "project"}
MIGRATED_REDMINE_HEADER_RE = re.compile(
    r"^\s*\*\*Reported by .*?Migrated from .*?\*\*\s*(?:---\s*)?",
    re.IGNORECASE | re.DOTALL,
)
REDMINE_METADATA_BLOCK_RE = re.compile(r"```\s*Redmine metadata:.*?```", re.IGNORECASE | re.DOTALL)
REDMINE_COMMENT_HEADER_RE = re.compile(r"^\s*\*\*Comment by .*?\*\*\s*", re.IGNORECASE | re.MULTILINE)
STRUCTURAL_TOKENS = {
    "---",
    "assignee",
    "comment",
    "comments",
    "date",
    "github.com",
    "http",
    "https",
    "metadata",
    "migrated",
    "redmine",
    "reported",
    "start",
    "updated",
}
PYTHON3_RE = re.compile(r"(?<![A-Za-z0-9_])python\s*3(?:\.\d+)?(?![0-9.])", re.IGNORECASE)
PYTHON27_RE = re.compile(r"(?<![A-Za-z0-9_])python\s*(?:==\s*)?2\.7(?![0-9.])", re.IGNORECASE)
TOPIC_LABELS = {
    "python_3_compatibility": "Python 3 compatibility",
    "terminal_progress": "terminal/progress output",
    "pretrained_artifacts": "pretrained artifacts",
    "visualization": "visualization workflow",
    "preprocessing": "dataset preprocessing",
    "training_evaluation": "training or evaluation behavior",
    "model_architecture": "model architecture",
    "usage_documentation": "usage or documentation",
    "data_quality": "data completeness or correctness",
    "database_schema": "database or schema behavior",
}


def _validate_directive() -> None:
    """Read the trusted SOP and verify its required classification contract."""
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    missing = [category for category in CATEGORIES if category not in directive]
    if missing:
        raise RuntimeError(f"Triage directive is missing categories: {', '.join(missing)}")
    for script in ("fetch_issues.py", "fetch_prs.py"):
        if script not in directive:
            raise RuntimeError(f"Triage directive does not name {script}.")


def _read_snapshot(path: Path, expected_resource: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read snapshot {path}: {error}") from error
    if not isinstance(payload, dict) or payload.get("resource") != expected_resource:
        raise RuntimeError(f"Snapshot {path} is not a {expected_resource} snapshot.")
    return payload


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(timestamp: Any, as_of: date) -> int | None:
    """Return elapsed days from a supplied GitHub timestamp, or ``None`` if invalid."""
    parsed = _parse_timestamp(timestamp)
    return (as_of - parsed.date()).days if parsed else None


def _safe_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").replace("`", "'").split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def _comment_metadata_note(comments: Any) -> str:
    """Describe the supplied comment metadata without implying body access."""
    if isinstance(comments, int):
        return f"The snapshot reports {comments} comment(s); comment bodies were not fetched."
    return "The snapshot does not provide a usable comment count; comment bodies were not fetched."


def _duplicate_text(item: dict[str, Any]) -> str:
    """Remove generated tracker-migration scaffolding before similarity scoring."""
    body = str(item.get("body") or "")
    body = MIGRATED_REDMINE_HEADER_RE.sub("", body)
    body = REDMINE_METADATA_BLOCK_RE.sub("", body)
    body = REDMINE_COMMENT_HEADER_RE.sub("", body)
    return f"{item.get('title', '')}\n{body}"


def _tokens(item: dict[str, Any]) -> set[str]:
    """Return content tokens only; URLs, dates, and tracker wrappers carry no issue semantics."""
    return {
        token.lower()
        for token in TOKEN_RE.findall(_duplicate_text(item))
        if token.lower() not in STOPWORDS | STRUCTURAL_TOKENS and not token.isdigit()
    }


def _duplicate_targets(issues: list[dict[str, Any]]) -> dict[int, int]:
    """Find only near-identical substantive reports; related topics remain separate."""
    ordered = sorted(issues, key=lambda item: (item.get("created_at") or "", item.get("number") or 0))
    targets: dict[int, int] = {}
    known: list[tuple[dict[str, Any], set[str]]] = []
    for issue in ordered:
        current_tokens = _tokens(issue)
        best: tuple[float, dict[str, Any]] | None = None
        for previous, previous_tokens in known:
            shared = current_tokens & previous_tokens
            smallest_report = min(len(current_tokens), len(previous_tokens))
            denominator = smallest_report or 1
            overlap = len(shared) / denominator
            if (
                smallest_report >= 12
                and len(shared) >= 8
                and overlap >= 0.75
                and (best is None or overlap > best[0])
            ):
                best = (overlap, previous)
        if best and isinstance(issue.get("number"), int) and isinstance(best[1].get("number"), int):
            targets[issue["number"]] = best[1]["number"]
        known.append((issue, current_tokens))
    return targets


def _topic_names(item: dict[str, Any]) -> list[str]:
    """Assign conservative semantic topics from issue metadata; topics are not duplicates."""
    text = f"{item.get('title', '')}\n{item.get('body', '')}"
    lowered = text.lower()
    topics: list[str] = []
    if PYTHON3_RE.search(text):
        topics.append("python_3_compatibility")
    title_lowered = str(item.get("title") or "").lower()
    if any(marker in title_lowered for marker in ("missing", "incorrect", "wrong", "doubled", "duplicate", "outdated", "not up-to-date")):
        topics.append("data_quality")
    if any(marker in lowered for marker in ("database", "schema", "sqlalchemy", "migration", "postgres", "sqlite")):
        topics.append("database_schema")
    if "stty" in lowered or "os.popen" in lowered:
        topics.append("terminal_progress")
    if any(marker in lowered for marker in ("pretrain", "pre-trained", "weights", ".t7", "vgg19")):
        topics.append("pretrained_artifacts")
    if "visualize" in lowered or "visualization" in lowered:
        topics.append("visualization")
    if any(marker in lowered for marker in ("preprocess", "reshape", ".h5", "data_pixel")):
        topics.append("preprocessing")
    if any(marker in lowered for marker in ("validation", "publictest", "privatetest", "k_fold", "k fold")) or (
        "accuracy" in lowered and any(marker in lowered for marker in ("train", "test set", "model", "epoch", "metric"))
    ):
        topics.append("training_evaluation")
    if any(marker in lowered for marker in ("resnet", "vgg", "avgpool", "linear layer")):
        topics.append("model_architecture")
    if any(marker in lowered for marker in ("how to use", "video", "paper", "citation", "input")):
        topics.append("usage_documentation")
    return topics


def _semantic_clusters(issues: list[dict[str, Any]]) -> dict[str, list[int]]:
    clusters: dict[str, list[int]] = {}
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        for topic in _topic_names(issue):
            clusters.setdefault(topic, []).append(number)
    return {topic: sorted(numbers) for topic, numbers in clusters.items() if len(numbers) >= 2}


def _issue_topic_rationale(issue: dict[str, Any], topic: str | None, cluster: list[int] | None, health_context: dict[str, Any], related_python_prs: list[int]) -> tuple[str, str]:
    number = issue.get("number")
    comments = issue.get("comments")
    subject = _safe_text(issue.get("body") or issue.get("title"), 150)
    subject_prefix = f"Issue #{number} specifically reports: '{subject}'. "
    comment_note = _comment_metadata_note(comments)
    if topic == "python_3_compatibility":
        related = ", ".join(f"#{item}" for item in (cluster or []) if item != number)
        rationale = subject_prefix + f"It asks about Python 3 compatibility. {comment_note} It is semantically related to {related or 'no other supplied issue'} but is not treated as a duplicate because it asks a distinct compatibility question."
        if health_context.get("python_27_eol"):
            rationale += " The supplied Health Report independently identifies the repository's declared Python 2.7 runtime as end-of-life, making this a documented revival concern rather than an age-based classification."
        next_step = "Group the Python 3 reports for one compatibility decision; review the related implementation PR(s) and define a supported runtime before closing any individual question."
        return rationale, next_step
    if topic == "data_quality":
        return (
            subject_prefix + f"Its title identifies a data-quality concern ({_safe_text(issue.get('title'), 120)}), rather than a demonstrated runtime failure. {comment_note} The issue remains independently actionable despite related data reports.",
            "Compare the named records or descriptions with the authoritative game-data source, identify the affected import/source file, and only then consolidate genuinely overlapping corrections.",
        )
    if topic == "database_schema":
        return (
            subject_prefix + f"It is about database or schema behavior. {comment_note} The supplied issue metadata does not establish the database backend, query, or schema version needed to assess reproducibility.",
            "Obtain the database backend, schema version, and minimal query or migration context; compare it with the documented schema before deciding whether to reproduce or consolidate it.",
        )
    if topic == "terminal_progress":
        return (
            subject_prefix + f"It concerns terminal/progress output. {comment_note} Its classification is based on the reported `stty`/terminal failure, not on a conclusion about whether a later comment resolved it.",
            "Reproduce the terminal behavior in the intended environment and cross-check the related terminal-output reports before deciding on a documentation or code follow-up.",
        )
    if topic == "pretrained_artifacts":
        return (
            subject_prefix + f"It concerns availability or loading of pretrained artifacts. {comment_note} The supplied metadata alone cannot establish whether the requested asset is available.",
            "Confirm which pretrained artifacts are intentionally published, their model compatibility, and their documented locations before responding or consolidating related requests.",
        )
    if topic == "visualization":
        return (
            subject_prefix + f"It concerns the visualization workflow. {comment_note} The supplied issue text does not include an environment reproduction for this specific report.",
            "Reproduce the visualization path with the declared legacy environment and record the exact model/input prerequisites before deciding whether the report is actionable.",
        )
    if topic == "preprocessing":
        return (
            subject_prefix + f"It concerns dataset preprocessing. {comment_note} The supplied metadata does not independently reproduce or invalidate its data-shape/preprocessing concern.",
            "Check the expected input file, preprocessing output shape, and dataset preparation documentation before deciding whether to clarify or reproduce the issue.",
        )
    if topic == "training_evaluation":
        return (
            subject_prefix + f"It concerns training or evaluation behavior. {comment_note} The supplied issue metadata does not establish an explanation for the reported metric, fold, or validation behavior.",
            "Compare the reported behavior with a controlled dataset split and document the intended training/evaluation semantics before taking further action.",
        )
    if topic == "model_architecture":
        return (
            subject_prefix + f"It concerns model architecture or model selection. {comment_note} The supplied issue metadata does not establish an answer to the specific design question.",
            "Review the relevant model implementation and document the intended architecture/weight compatibility before responding or consolidating similar questions.",
        )
    if topic == "usage_documentation":
        return (
            subject_prefix + f"It asks for usage or documentation guidance. {comment_note} This snapshot alone cannot establish whether the requested guidance exists elsewhere in the project.",
            "Use the regenerated documentation evidence to decide whether the missing guidance can be stated accurately without claiming unverified runtime support.",
        )
    return (
        subject_prefix + f"The title ({_safe_text(issue.get('title'), 120)}) identifies the requested outcome. {comment_note} The supplied metadata does not establish that the underlying behavior has been reproduced or resolved.",
        "Start with the item’s stated outcome and affected component, then collect the smallest reproducible example or authoritative source needed to decide whether it is actionable, duplicate, or obsolete.",
    )


def _issue_record(
    issue: dict[str, Any],
    duplicate_of: int | None,
    clusters: dict[str, list[int]],
    health_context: dict[str, Any],
    related_python_prs: list[int],
) -> dict[str, Any]:
    state = issue.get("state")
    comments = issue.get("comments")
    text = f"{issue.get('title', '')}\n{issue.get('body', '')}"
    textual_resolution = TEXTUAL_RESOLUTION_RE.search(text)
    evidence = [
        f"GitHub state: `{state}`; comment count: `{comments}`; last update: `{issue.get('updated_at')}`. Comment bodies were not fetched.",
        f"Reported content: {_safe_text(issue.get('body') or issue.get('title'))}",
    ]
    topics = _topic_names(issue)
    primary_topic = topics[0] if topics else None
    cluster = clusters.get(primary_topic) if primary_topic else None
    if cluster:
        evidence.append(f"Semantic cluster `{TOPIC_LABELS[primary_topic]}`: related open issue(s) {', '.join(f'#{item}' for item in cluster if item != issue.get('number')) or '(none)'}. These are related, not assumed duplicates.")
    if primary_topic == "python_3_compatibility" and health_context.get("python_27_eol"):
        evidence.append(f"Health Report context: `{health_context['path']}` records the README-declared Python 2.7 runtime as end-of-life.")
        if related_python_prs:
            evidence.append(f"Related open PR evidence: {', '.join(f'#{item}' for item in related_python_prs)} contains Python 3 compatibility work and must be reviewed on its own merits.")
    if duplicate_of is not None:
        category = "duplicate/resolved"
        confidence = "medium"
        rationale = f"Issue #{issue.get('number')} ({_safe_text(issue.get('title'), 120)}) has high supplied-text overlap with open issue #{duplicate_of}. That is evidence for a possible duplicate relationship, not proof that either report’s underlying problem is resolved."
        next_step = f"Review against issue #{duplicate_of}; consolidate only after a human confirms the reports describe the same condition."
    elif state == "closed":
        category = "duplicate/resolved"
        confidence = "medium"
        rationale = f"Issue #{issue.get('number')} ({_safe_text(issue.get('title'), 120)}) is supplied in a closed state. This is a closed state, not a text-based resolution signal; the snapshot provides no closure reason, and comment bodies were not fetched."
        next_step = "Review the closure record and any linked fix or duplicate before treating the underlying concern as resolved."
    elif textual_resolution:
        category = "duplicate/resolved"
        confidence = "low"
        evidence.append(f"Explicit title/body resolution signal: `{_safe_text(textual_resolution.group(0), 100)}`.")
        rationale = f"Issue #{issue.get('number')} ({_safe_text(issue.get('title'), 120)}) contains the explicit title/body signal `{_safe_text(textual_resolution.group(0), 100)}`. This is a text-based signal only, not independently verified resolution; comment bodies were not fetched."
        next_step = "Locate and verify the cited fix or duplicate before taking any action."
    else:
        category = "still valid"
        confidence = "medium" if len(str(issue.get("body") or "")) >= 60 or (comments or 0) > 0 else "low"
        rationale, next_step = _issue_topic_rationale(issue, primary_topic, cluster, health_context, related_python_prs)
    return {
        "kind": "Issue",
        "number": issue.get("number"),
        "title": issue.get("title"),
        "category": category,
        "confidence": confidence,
        "evidence": evidence,
        "rationale": rationale,
        "recommended_human_next_step": next_step,
        "url": issue.get("html_url"),
    }


def _pull_request_record(pull_request: dict[str, Any], detail: dict[str, Any] | None, as_of: date) -> dict[str, Any]:
    source = detail or pull_request
    state = source.get("state")
    inactivity_days = _days_since(source.get("updated_at"), as_of)
    title = str(source.get("title") or "")
    description = str(source.get("description") if detail else source.get("body") or "")
    intent = bool(CHANGE_INTENT_RE.search(f"{title}\n{description}"))
    merge_status = (detail or {}).get("merge_status")
    comments = (detail or {}).get("comment_counts") or {
        "issue_comments": source.get("comments"),
        "review_comments": source.get("review_comments"),
    }
    evidence = [
        f"GitHub state: `{state}`; created: `{source.get('created_at')}`; updated: `{source.get('updated_at')}`; inactivity since last update at assessment: `{inactivity_days}` day(s).",
        f"Title: {_safe_text(title)}",
        f"Description: {_safe_text(description) if description else '(empty)'}",
        f"Issue comment count: `{comments.get('issue_comments')}`; review comment count: `{comments.get('review_comments')}`. Issue and review comment bodies were not fetched.",
    ]
    if merge_status is not None:
        evidence.append(
            f"GitHub merge fields: mergeable=`{merge_status.get('mergeable')}`, mergeable_state=`{merge_status.get('mergeable_state')}`, conflict_state=`{merge_status.get('conflict_state')}`, merged=`{merge_status.get('merged')}`."
        )
    else:
        evidence.append("Mergeability is unavailable in the supplied snapshot; it is not inferred from PR age.")

    if state != "open":
        category = "duplicate/resolved"
        confidence = "medium"
        rationale = f"PR #{source.get('number')} ({_safe_text(title, 120)}) is not open, so it is no longer an active backlog candidate in this snapshot. That status does not by itself establish whether its proposed change was adopted."
        next_step = "Review the closing or merge record before treating the underlying change as resolved."
    elif intent and inactivity_days is not None and inactivity_days >= 365:
        category = "valuable-but-stalled-by-inactivity"
        confidence = "high" if detail is not None else "medium"
        proposal = _safe_text(description or title, 150)
        if merge_status and merge_status.get("conflict_state") == "conflicted":
            rationale = f"PR #{source.get('number')} proposes: '{proposal}'. It is long-inactive and GitHub currently reports a merge conflict; that is a refresh/review task for this proposal, not evidence that it is obsolete."
            next_step = "Review the patch intent and rebase or resolve conflicts in an operator-controlled fork before deciding whether to retain it."
        elif merge_status and merge_status.get("mergeable") is True:
            rationale = f"PR #{source.get('number')} proposes: '{proposal}'. It is long-inactive but GitHub currently reports it as mergeable, so its lack of recent updates does not make this specific change obsolete."
            next_step = "Review the diff and validate the change in an operator-controlled fork before deciding whether to adopt it."
        else:
            rationale = f"PR #{source.get('number')} proposes: '{proposal}'. It is long-inactive, and the available snapshot does not establish that this specific change is obsolete."
            next_step = "Fetch current mergeability if absent, then review the diff and validate it in an operator-controlled fork."
    else:
        category = "still valid"
        confidence = "low" if not description else "medium"
        rationale = f"PR #{source.get('number')} remains open, but its supplied title/description ({_safe_text(description or title, 150)}) does not provide enough concrete intent to elevate this particular item as valuable stalled work."
        if intent and inactivity_days is None:
            rationale += " Its `updated_at` timestamp is missing or invalid, so the supplied snapshot cannot establish long inactivity."
        next_step = "Review the diff, scope, and current mergeability before deciding whether to pursue or close it."
    return {
        "kind": "Pull request",
        "number": source.get("number"),
        "title": source.get("title"),
        "category": category,
        "confidence": confidence,
        "evidence": evidence,
        "rationale": rationale,
        "recommended_human_next_step": next_step,
        "url": source.get("html_url"),
    }


def triage(issues_snapshot: dict[str, Any], prs_snapshot: dict[str, Any], as_of: date, health_context: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify every supplied issue and PR without performing a remote mutation."""
    issues = [item for item in issues_snapshot.get("issues", []) if isinstance(item, dict)]
    pull_requests = [item for item in prs_snapshot.get("pull_requests", []) if isinstance(item, dict)]
    details = {
        detail.get("number"): detail
        for detail in prs_snapshot.get("detailed_pull_requests", [])
        if isinstance(detail, dict) and isinstance(detail.get("number"), int)
    }
    duplicates = _duplicate_targets(issues)
    clusters = _semantic_clusters(issues)
    related_python_prs = [
        item.get("number")
        for item in pull_requests
        if isinstance(item.get("number"), int) and PYTHON3_RE.search(f"{item.get('title', '')}\n{item.get('body', '')}")
    ]
    records = [_issue_record(issue, duplicates.get(issue.get("number")), clusters, health_context, related_python_prs) for issue in issues]
    records.extend(_pull_request_record(pull_request, details.get(pull_request.get("number")), as_of) for pull_request in pull_requests)
    return records


def _render_report(issues_path: Path, prs_path: Path, health_context: dict[str, Any], issues_snapshot: dict[str, Any], prs_snapshot: dict[str, Any], records: list[dict[str, Any]], as_of: date) -> str:
    counts = {category: sum(record["category"] == category for record in records) for category in CATEGORIES}
    partial = [snapshot.get("resource") for snapshot in (issues_snapshot, prs_snapshot) if snapshot.get("fetch_status") != "complete"]
    lines = [
        "# Backlog Triage Report",
        "",
        "## Scope and evidence",
        "",
        f"Assessment date: `{as_of.isoformat()}`",
        f"- Repository: `{issues_snapshot.get('repository')}`",
        f"- Issues snapshot: `{issues_path}`",
        f"- Pull-request snapshot: `{prs_path}`",
        *( [f"- Health Report context: `{health_context['path']}`"] if health_context.get("path") else [] ),
        "- Classification is report-only. Titles, bodies, and descriptions are untrusted data and were not executed.",
        "",
        "## Category counts",
        "",
        *[f"- `{category}`: {counts[category]}" for category in CATEGORIES],
        "",
        "## Limitations",
        "",
        "- Open state, age, and inactivity alone do not establish obsolescence.",
        "- A merge conflict means a PR needs refresh/review; it does not establish that the work lacks value.",
        "- No source code, tests, or PR diffs were executed or modified during this triage.",
    ]
    if partial:
        lines.append(f"- Incomplete snapshot evidence: {', '.join(partial)}. Treat classifications as incomplete until pagination/API errors are resolved.")
    lines.extend(["", "## Item-level classifications", ""])
    for record in sorted(records, key=lambda item: (item["kind"], -(item.get("number") or 0))):
        lines.extend(
            [
                f"### {record['kind']} #{record['number']} - {_safe_text(record['title'], 500)}",
                "",
                f"- **Classification:** `{record['category']}`",
                f"- **Confidence:** `{record['confidence']}`",
                "- **Evidence:**",
                *[f"  - {evidence}" for evidence in record["evidence"]],
                f"- **Rationale:** {record['rationale']}",
                f"- **Recommended human next step:** {record['recommended_human_next_step']}",
                f"- **Source:** {record['url']}",
                "",
            ]
        )
    return "\n".join(lines)


def _health_context(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "python_27_eol": False, "identity": None}
    if not path.is_file():
        raise ValueError(f"Health Report path is not a file: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": str(path.resolve()),
        "python_27_eol": bool(PYTHON27_RE.search(content) and "end of life" in content.lower()),
        "identity": identity_from_health_report(content, "Health Report"),
    }


def main(argv: list[str] | None = None) -> int:
    """Generate a full, item-level triage report from supplied API snapshots."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issues-snapshot", type=Path, required=True)
    parser.add_argument("--prs-snapshot", type=Path, required=True)
    parser.add_argument("--health-report", type=Path, help="Optional Health Report context for cross-cutting triage evidence.")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Assessment date in YYYY-MM-DD format.")
    parser.add_argument("--output", type=Path, required=True, help="Markdown report destination.")
    args = parser.parse_args(argv)
    try:
        _validate_directive()
        as_of = date.fromisoformat(args.as_of)
        issues_snapshot = _read_snapshot(args.issues_snapshot, "issues")
        prs_snapshot = _read_snapshot(args.prs_snapshot, "pull_requests")
        health_context = _health_context(args.health_report)
        identities = [
            identity_from_json(issues_snapshot, "issues snapshot", require_remote=True),
            identity_from_json(prs_snapshot, "pull-request snapshot", require_remote=True),
        ]
        if health_context["identity"] is not None:
            identities.append(health_context["identity"])
        require_same_repository(*identities)
        records = triage(issues_snapshot, prs_snapshot, as_of, health_context)
        report = _render_report(args.issues_snapshot.resolve(), args.prs_snapshot.resolve(), health_context, issues_snapshot, prs_snapshot, records, as_of)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
