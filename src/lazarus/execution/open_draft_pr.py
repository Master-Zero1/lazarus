"""Preview or create the documentation-only draft PR described in ``draft_pr.md``.

Directive: ``directives/draft_pr.md``.

The default mode is a local, network-free preview.  A live GitHub request is
deliberately unavailable unless the caller supplies both ``--execute`` and the
explicit ``I_APPROVE_DRAFT_PR`` approval token.  The live path verifies that
the target is an operator-owned fork, creates only a new draft PR, and never
updates application files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DIRECTIVE_PATH = Path("directives/draft_pr.md")
ALLOWED_DOCUMENTS = ("README.md", "ARCHITECTURE.md", "CONTRIBUTING.md")
APPROVAL_TOKEN = "I_APPROVE_DRAFT_PR"
GITHUB_API = "https://api.github.com"
USER_AGENT = "lazarus-documentation-pr/1.0"
DEFAULT_BRANCH = "lazarus/regenerated-documentation"
DEFAULT_TITLE = "docs: regenerate repository documentation from code inventory"
REPOSITORY_FULL_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
HEALTH_REPORT_SCOPE_RE = re.compile(r"^Repository inspected: `([^`]+)`$", re.MULTILINE)
HEALTH_REPORT_UPSTREAM_RE = re.compile(r"^Repository canonical upstream: `([^`]+)`$", re.MULTILINE)


class DraftPrError(RuntimeError):
    """Raised when the SOP's safety preconditions are not met."""


class GitHubApiError(DraftPrError):
    """A GitHub response that makes it unsafe to continue."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__("GitHub API request failed with HTTP {0}: {1}".format(status, message))
        self.status = status
        self.message = message


@dataclass(frozen=True)
class HealthReport:
    """Trusted, target-scoped findings that may be carried into a draft PR body."""

    path: Path
    repository_name: str
    checkout_path: Path
    constraints: tuple[str, ...]


@dataclass(frozen=True)
class DocumentationFile:
    """A reviewed, allowlisted documentation file ready for a Git tree."""

    name: str
    path: Path
    content: str
    content_bytes: bytes
    sha256: str
    git_blob_sha: str

    def preview(self) -> dict[str, Any]:
        """Return the deterministic public metadata used in the creation receipt."""
        return {
            "source_path": str(self.path),
            "target_path": self.name,
            "bytes": len(self.content_bytes),
            "sha256": self.sha256,
            "git_blob_sha": self.git_blob_sha,
        }


def _health_report_constraints(content: str) -> tuple[str, ...]:
    """Select only Health Report facts that constrain compatibility or verification."""
    observed_facts = re.search(r"^## Observed Facts\s*$\n(.*?)(?=^## |\Z)", content, re.MULTILINE | re.DOTALL)
    if observed_facts is None:
        raise DraftPrError("Health Report is missing its Observed Facts section.")

    constraints: list[str] = []
    for line in observed_facts.group(1).splitlines():
        if not line.startswith("- "):
            continue
        finding = line[2:].strip()
        if (
            finding.startswith("Freshness assessment classifies ")
            or finding.startswith("CI finding ")
            or finding.startswith("No CI configuration was found")
            or finding.startswith("No conventional dependency manifest was found")
        ):
            constraints.append(finding)
    return tuple(dict.fromkeys(constraints))


def load_health_report(path: Path, expected_upstream: str) -> HealthReport:
    """Load target-scoped diagnosis facts without interpreting repo content as instructions."""
    if not path.is_file() or path.is_symlink():
        raise DraftPrError("Health Report must be a regular file: {0}".format(path))
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DraftPrError("Health Report is not UTF-8 text: {0}".format(path)) from exc

    scope = HEALTH_REPORT_SCOPE_RE.search(content)
    if scope is None:
        raise DraftPrError("Health Report does not identify its inspected repository.")
    inspected_path = scope.group(1).rstrip("\\/")
    repository_name = re.split(r"[\\/]", inspected_path)[-1]
    canonical_upstream = HEALTH_REPORT_UPSTREAM_RE.search(content)
    if canonical_upstream is None:
        raise DraftPrError(
            "Health Report does not declare a canonical upstream; regenerate diagnosis with --expected-upstream before draft-PR processing."
        )
    reported_upstream = canonical_upstream.group(1)
    if REPOSITORY_FULL_NAME_RE.fullmatch(reported_upstream) is None:
        raise DraftPrError("Health Report canonical upstream is not an owner/name repository identifier.")
    if reported_upstream.casefold() != expected_upstream.casefold():
        raise DraftPrError(
            "Health Report canonical upstream {0!r} does not match expected upstream {1!r}.".format(
                reported_upstream, expected_upstream
            )
        )
    upstream_repository_name = reported_upstream.rsplit("/", 1)[1]
    if repository_name.casefold() != upstream_repository_name.casefold():
        raise DraftPrError(
            "Health Report target {0!r} does not match expected upstream repository {1!r}.".format(
                repository_name, upstream_repository_name
            )
        )
    return HealthReport(
        path=path.resolve(),
        repository_name=repository_name,
        checkout_path=Path(inspected_path).resolve(),
        constraints=_health_report_constraints(content),
    )


def _read_docs_evidence(path: Path) -> Path:
    """Require a regular, readable UTF-8 evidence note without reproducing it in the PR body."""
    if not path.is_file() or path.is_symlink():
        raise DraftPrError("Documentation evidence must be a regular file: {0}".format(path))
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DraftPrError("Documentation evidence is not UTF-8 text: {0}".format(path)) from exc
    except OSError as exc:
        raise DraftPrError("Documentation evidence could not be read: {0}: {1}".format(path, exc)) from exc
    return path.resolve()


def build_pr_body(health_report: HealthReport, docs_evidence_path: Path) -> str:
    """Build a reviewable body from the supplied target repository's Health Report."""
    constraints = "\n".join("- {0}".format(finding) for finding in health_report.constraints)
    if not constraints:
        constraints = "- The supplied Health Report records no specific legacy compatibility or CI constraint."
    return """## Regenerated documentation (draft)

This draft updates only repository documentation regenerated from a static
code-structure inventory and the Lazarus Health Report.

### Scope

- `README.md`
- `ARCHITECTURE.md`
- `CONTRIBUTING.md`

### Documentation evidence reviewed

- Claim-to-source mapping: `{evidence_name}` (reviewed local artifact; not committed by this PR).

### Known legacy constraints carried forward

{constraints}

No application source code, dependency manifest, test, CI, license, or
configuration file was changed.
""".format(constraints=constraints, evidence_name=docs_evidence_path.name.replace("`", "'"))


def _git_blob_sha(content: bytes) -> str:
    """Compute the SHA-1 GitHub assigns to a UTF-8 blob with this content."""
    header = "blob {0}\0".format(len(content)).encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _read_document(path: Path, docs_root: Path) -> DocumentationFile:
    """Read one regular UTF-8 allowlisted document without following links."""
    if not path.exists():
        raise DraftPrError("Required documentation file is missing: {0}".format(path))
    if not path.is_file() or path.is_symlink():
        raise DraftPrError("Documentation candidate must be a regular file, not a link: {0}".format(path))

    resolved_root = docs_root.resolve()
    resolved_file = path.resolve()
    try:
        resolved_file.relative_to(resolved_root)
    except ValueError as exc:
        raise DraftPrError("Documentation candidate escapes its draft directory: {0}".format(path)) from exc

    try:
        content_bytes = path.read_bytes()
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DraftPrError("Documentation candidate is not UTF-8 text: {0}".format(path)) from exc

    return DocumentationFile(
        name=path.name,
        path=resolved_file,
        content=content,
        content_bytes=content_bytes,
        sha256=hashlib.sha256(content_bytes).hexdigest(),
        git_blob_sha=_git_blob_sha(content_bytes),
    )


def collect_documents(docs_dir: Path, requested_candidates: Iterable[str] | None) -> tuple[list[DocumentationFile], list[str]]:
    """Validate the exact documentation allowlist and load its three reviewed files.

    Non-candidate evidence files in the draft directory are reported as excluded;
    they are never implicitly added to a commit.  Any explicit candidate outside
    the allowlist, or omission of an allowlisted file, stops the operation.
    """
    if not docs_dir.exists() or not docs_dir.is_dir():
        raise DraftPrError("Documentation draft directory does not exist: {0}".format(docs_dir))
    if docs_dir.is_symlink():
        raise DraftPrError("Documentation draft directory must not be a symlink: {0}".format(docs_dir))

    candidates = list(requested_candidates) if requested_candidates else list(ALLOWED_DOCUMENTS)
    if len(candidates) != len(set(candidates)):
        raise DraftPrError("Candidate set contains a duplicate path.")
    unsupported = sorted(set(candidates) - set(ALLOWED_DOCUMENTS))
    missing = sorted(set(ALLOWED_DOCUMENTS) - set(candidates))
    if unsupported:
        raise DraftPrError(
            "Candidate set contains non-documentation file(s): {0}. Allowed files are: {1}".format(
                ", ".join(unsupported), ", ".join(ALLOWED_DOCUMENTS)
            )
        )
    if missing:
        raise DraftPrError(
            "Candidate set must contain all reviewed documentation files; missing: {0}".format(
                ", ".join(missing)
            )
        )

    documents = [_read_document(docs_dir / name, docs_dir) for name in ALLOWED_DOCUMENTS]
    excluded = sorted(
        entry.name
        for entry in docs_dir.iterdir()
        if entry.name not in ALLOWED_DOCUMENTS
    )
    return documents, excluded


def _validate_branch_name(branch: str) -> None:
    """Reject unsafe or ambiguous Git ref names before they enter an API path."""
    if (
        not branch
        or branch.startswith("/")
        or branch.endswith("/")
        or ".." in branch
        or "@{" in branch
        or branch.endswith(".lock")
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch)
    ):
        raise DraftPrError("Unsafe branch name: {0!r}".format(branch))


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_output_path(
    output_path: Path | None,
    docs_draft_dir: Path,
    health_report_path: Path,
    docs_evidence_path: Path,
) -> None:
    """Reject receipt destinations that would overwrite supplied documentation evidence."""
    if output_path is None:
        return
    if _is_within(output_path, docs_draft_dir):
        raise DraftPrError(
            "Output path {0} is inside documentation draft directory {1}.".format(
                output_path.resolve(), docs_draft_dir.resolve()
            )
        )
    if output_path.resolve() == health_report_path.resolve():
        raise DraftPrError(
            "Output path {0} collides with input Health Report {1}.".format(
                output_path.resolve(), health_report_path.resolve()
            )
        )
    if output_path.resolve() == docs_evidence_path.resolve():
        raise DraftPrError(
            "Output path {0} collides with input documentation evidence {1}.".format(
                output_path.resolve(), docs_evidence_path.resolve()
            )
        )


def _validate_output_against_checkout(output_path: Path | None, health_report: HealthReport) -> None:
    """Reject receipts that would be written into the checkout named by the report."""
    if output_path is not None and _is_within(output_path, health_report.checkout_path):
        raise DraftPrError(
            "Output path {0} is inside inspected repository checkout {1}.".format(
                output_path.resolve(), health_report.checkout_path
            )
        )


def _write_receipt(receipt: dict[str, Any], output: Path | None) -> None:
    """Print a receipt and optionally persist the same structured result locally."""
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


def _write_halted_receipt(receipt: dict[str, Any], output: Path | None) -> None:
    """Emit a halted receipt, falling back to stdout when persistence fails.

    ``output`` is passed only when it has passed the destination safety checks
    and has not already failed during this invocation.  A failed receipt
    destination must never be retried in the error path: stdout remains the
    safe structured fallback.
    """
    try:
        _write_receipt(receipt, output)
    except OSError as exc:
        fallback = dict(receipt)
        fallback["error"] = "{0}; additionally, the halted receipt could not be written to {1}: {2}".format(
            receipt["error"], output, exc
        )
        _write_receipt(fallback, None)


def build_preview(
    args: argparse.Namespace,
    documents: list[DocumentationFile],
    excluded: list[str],
    health_report: HealthReport,
    docs_evidence_path: Path,
) -> dict[str, Any]:
    """Build the exact plan without reading a token or calling GitHub."""
    return {
        "mode": "preview",
        "status": "awaiting_operator_approval",
        "directive": str(DIRECTIVE_PATH),
        "target": {
            "fork_owner": args.fork_owner,
            "fork_repo": args.fork_repo,
            "base_branch": args.base,
            "new_branch": args.branch,
            "draft": True,
            "expected_upstream": args.expected_upstream,
        },
        "health_report": {"path": str(health_report.path), "repository": health_report.repository_name},
        "documentation_evidence": {"path": str(docs_evidence_path)},
        "pull_request": {
            "title": args.title,
            "body": build_pr_body(health_report, docs_evidence_path),
        },
        "candidate_files": [document.preview() for document in documents],
        "excluded_non_candidates": excluded,
        "safety_checks_before_remote_write": [
            "explicit --execute and exact operator approval token",
            "authenticated repository owner matches --fork-owner",
            "repository is a fork of {0}".format(args.expected_upstream),
            "authenticated caller has push permission",
            "base branch exists and is the repository default branch",
            "new branch does not already exist and no open documentation PR from this fork matches the branch or title",
            "at least one allowlisted document differs from the base tree",
        ],
        "approval_required": "Run again with --execute --operator-approval {0}".format(APPROVAL_TOKEN),
        "remote_side_effects": "none; preview mode does not contact GitHub",
    }


def _request_json(
    method: str,
    endpoint: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Call the GitHub REST API with a narrow JSON interface and clear failures."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        GITHUB_API + endpoint,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer {0}".format(token),
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            error_data = json.loads(exc.read().decode("utf-8"))
            message = str(error_data.get("message", "GitHub rejected the request")) if isinstance(error_data, dict) else "GitHub rejected the request"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            message = "GitHub rejected the request"
        raise GitHubApiError(exc.code, message) from exc
    except OSError as exc:
        raise GitHubApiError(0, "network failure: {0}".format(exc)) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(0, "GitHub returned non-JSON data") from exc


def _get_or_none(endpoint: str, token: str) -> Any | None:
    """Return ``None`` only for a GitHub 404; preserve all other failures."""
    try:
        return _request_json("GET", endpoint, token)
    except GitHubApiError as exc:
        if exc.status == 404:
            return None
        raise


def _require_dict(value: Any, description: str) -> dict[str, Any]:
    """Defensively validate a JSON object before relying on its fields."""
    if not isinstance(value, dict):
        raise GitHubApiError(0, "Unexpected GitHub response for {0}".format(description))
    return value


def _require_string(value: Any, description: str) -> str:
    """Defensively validate a required string response field."""
    if not isinstance(value, str) or not value:
        raise GitHubApiError(0, "GitHub response lacks {0}".format(description))
    return value


def _is_from_confirmed_fork(pull_request: dict[str, Any], fork_owner: str, fork_repo: str) -> bool:
    """Accept only an open PR whose head repository is the confirmed fork itself."""
    head = pull_request.get("head")
    if not isinstance(head, dict):
        return False
    head_repository = head.get("repo")
    if not isinstance(head_repository, dict):
        return False
    full_name = head_repository.get("full_name")
    return isinstance(full_name, str) and full_name.casefold() == "{0}/{1}".format(fork_owner, fork_repo).casefold()


def _matching_documentation_pull_request(
    pull_request: dict[str, Any],
    fork_owner: str,
    fork_repo: str,
    branch: str,
    title: str,
) -> bool:
    """Identify a same-fork open PR that matches a known documentation branch or title."""
    if not _is_from_confirmed_fork(pull_request, fork_owner, fork_repo):
        return False
    head = pull_request.get("head")
    head_ref = head.get("ref") if isinstance(head, dict) else None
    known_branches = {DEFAULT_BRANCH, branch}
    known_titles = {DEFAULT_TITLE, title}
    return (isinstance(head_ref, str) and head_ref in known_branches) or pull_request.get("title") in known_titles


def _ensure_no_open_documentation_pull_request(repo_endpoint: str, token: str, args: argparse.Namespace) -> None:
    """Read every page of open PRs before a write, stopping on an existing documentation PR."""
    page = 1
    per_page = 100
    while True:
        query = urlencode({"state": "open", "per_page": per_page, "page": page})
        payload = _request_json("GET", repo_endpoint + "/pulls?" + query, token)
        if not isinstance(payload, list):
            raise GitHubApiError(0, "Unexpected GitHub response while checking existing documentation pull requests")
        for pull_request in payload:
            if not isinstance(pull_request, dict):
                continue
            if _matching_documentation_pull_request(
                pull_request, args.fork_owner, args.fork_repo, args.branch, args.title
            ):
                number = pull_request.get("number")
                existing_title = pull_request.get("title")
                raise DraftPrError(
                    "An open documentation pull request already exists from the confirmed fork"
                    " (#{0}, title {1!r}); human review is required before creating another.".format(
                        number if isinstance(number, int) else "unknown", existing_title
                    )
                )
        if len(payload) < per_page:
            return
        page += 1


def create_draft_pr(
    args: argparse.Namespace,
    documents: list[DocumentationFile],
    excluded: list[str],
    health_report: HealthReport,
    docs_evidence_path: Path,
) -> dict[str, Any]:
    """Perform the approved, documentation-only GitHub mutation sequence.

    All ownership, branch, and no-op checks complete before the first POST.  If
    a later request fails, the receipt reports the partial remote state and the
    function deliberately does not retry or clean up remotely.
    """
    if not args.execute:
        raise DraftPrError("Live creation requires --execute.")
    if args.operator_approval != APPROVAL_TOKEN:
        raise DraftPrError(
            "Live creation requires explicit --operator-approval {0}.".format(APPROVAL_TOKEN)
        )
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise DraftPrError("Live creation requires GITHUB_TOKEN; preview mode does not.")

    owner = quote(args.fork_owner, safe="")
    repo = quote(args.fork_repo, safe="")
    repo_endpoint = "/repos/{0}/{1}".format(owner, repo)
    remote = _require_dict(_request_json("GET", repo_endpoint, token), "repository metadata")
    remote_owner = _require_string(
        _require_dict(remote.get("owner"), "repository owner").get("login"), "repository owner login"
    )
    if remote_owner.casefold() != args.fork_owner.casefold():
        raise DraftPrError(
            "Target owner check failed: GitHub reports {0!r}, not requested operator {1!r}.".format(
                remote_owner, args.fork_owner
            )
        )
    if remote.get("fork") is not True:
        raise DraftPrError("Target repository is not a fork; refusing to operate on a possible upstream.")
    parent = _require_dict(remote.get("parent"), "fork parent")
    if str(parent.get("full_name") or "").casefold() != args.expected_upstream.casefold():
        raise DraftPrError(
            "Fork parent check failed: expected {0!r}, got {1!r}.".format(
                args.expected_upstream, parent.get("full_name")
            )
        )
    permissions = _require_dict(remote.get("permissions"), "authenticated permissions")
    if permissions.get("push") is not True:
        raise DraftPrError("Authenticated operator does not have push permission on the confirmed fork.")
    if remote.get("default_branch") != args.base:
        raise DraftPrError(
            "Requested base branch {0!r} is not GitHub's reported default branch {1!r}.".format(
                args.base, remote.get("default_branch")
            )
        )

    base_ref = _require_dict(
        _request_json("GET", repo_endpoint + "/git/ref/heads/" + quote(args.base, safe="/"), token),
        "base branch reference",
    )
    base_sha = _require_string(
        _require_dict(base_ref.get("object"), "base branch object").get("sha"), "base branch commit SHA"
    )
    _ensure_no_open_documentation_pull_request(repo_endpoint, token, args)
    branch_endpoint = repo_endpoint + "/git/ref/heads/" + quote(args.branch, safe="/")
    if _get_or_none(branch_endpoint, token) is not None:
        raise DraftPrError("Branch {0!r} already exists; human review is required before reuse.".format(args.branch))

    base_commit = _require_dict(
        _request_json("GET", repo_endpoint + "/git/commits/" + quote(base_sha, safe=""), token),
        "base commit",
    )
    base_tree_sha = _require_string(
        _require_dict(base_commit.get("tree"), "base commit tree").get("sha"), "base tree SHA"
    )
    base_tree = _require_dict(
        _request_json("GET", repo_endpoint + "/git/trees/" + quote(base_tree_sha, safe="") + "?recursive=1", token),
        "base tree",
    )
    entries = base_tree.get("tree")
    if not isinstance(entries, list) or base_tree.get("truncated") is True:
        raise DraftPrError("Unable to safely inspect the complete base tree for no-op documentation changes.")
    existing_blobs = {
        entry.get("path"): entry.get("sha")
        for entry in entries
        if isinstance(entry, dict) and entry.get("type") == "blob"
    }
    changed_documents = [document for document in documents if existing_blobs.get(document.name) != document.git_blob_sha]
    if not changed_documents:
        raise DraftPrError("All allowlisted documentation files already match the base branch; no PR was created.")

    receipt: dict[str, Any] = {
        "mode": "live",
        "status": "preconditions_passed",
        "target": {
            "fork_owner": args.fork_owner,
            "fork_repo": args.fork_repo,
            "base_branch": args.base,
            "new_branch": args.branch,
            "draft": True,
            "expected_upstream": args.expected_upstream,
        },
        "health_report": {"path": str(health_report.path), "repository": health_report.repository_name},
        "documentation_evidence": {"path": str(docs_evidence_path)},
        "pull_request": {"title": args.title, "body": build_pr_body(health_report, docs_evidence_path)},
        "candidate_files": [document.preview() for document in documents],
        "changed_files": [document.name for document in changed_documents],
        "excluded_non_candidates": excluded,
        "remote_side_effects": [],
    }

    try:
        tree_response = _require_dict(
            _request_json(
                "POST",
                repo_endpoint + "/git/trees",
                token,
                {
                    "base_tree": base_tree_sha,
                    "tree": [
                        {"path": document.name, "mode": "100644", "type": "blob", "content": document.content}
                        for document in changed_documents
                    ],
                },
            ),
            "created documentation tree",
        )
        tree_sha = _require_string(tree_response.get("sha"), "created tree SHA")
        receipt["documentation_tree_sha"] = tree_sha

        commit_response = _require_dict(
            _request_json(
                "POST",
                repo_endpoint + "/git/commits",
                token,
                {
                    "message": "docs: regenerate repository documentation from code inventory",
                    "tree": tree_sha,
                    "parents": [base_sha],
                },
            ),
            "created documentation commit",
        )
        commit_sha = _require_string(commit_response.get("sha"), "created commit SHA")
        receipt["created_commit_sha"] = commit_sha

        _request_json(
            "POST",
            repo_endpoint + "/git/refs",
            token,
            {"ref": "refs/heads/{0}".format(args.branch), "sha": commit_sha},
        )
        receipt["remote_side_effects"].append({"created_branch": args.branch, "commit_sha": commit_sha})

        pull_response = _require_dict(
            _request_json(
                "POST",
                repo_endpoint + "/pulls",
                token,
                {"title": args.title, "head": args.branch, "base": args.base, "body": build_pr_body(health_report, docs_evidence_path), "draft": True},
            ),
            "created pull request",
        )
        if pull_response.get("draft") is not True:
            raise DraftPrError("GitHub did not confirm a draft pull request; no merge or follow-up action was taken.")
        receipt["remote_side_effects"].append(
            {
                "created_draft_pull_request": pull_response.get("html_url"),
                "pull_request_number": pull_response.get("number"),
            }
        )
        receipt["status"] = "draft_pull_request_created"
        return receipt
    except Exception as exc:
        receipt["status"] = "partial_remote_failure"
        receipt["failure"] = str(exc)
        raise DraftPrError(json.dumps(receipt, indent=2, sort_keys=True)) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the narrow command-line contract for the Draft PR execution script."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docs_draft_dir", type=Path, help="Reviewed documentation draft directory.")
    parser.add_argument("--fork-owner", required=True, help="Operator-owned GitHub fork owner.")
    parser.add_argument("--fork-repo", required=True, help="Operator-owned GitHub fork repository name.")
    parser.add_argument("--expected-upstream", required=True, help="Expected parent repository as owner/name.")
    parser.add_argument("--health-report", type=Path, required=True, help="Target repository Health Report used to build the PR body.")
    parser.add_argument("--docs-evidence-path", type=Path, required=True, help="Readable documentation claim-to-source mapping reviewed for the PR body.")
    parser.add_argument("--base", default="master", help="Existing default branch to target (default: master).")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="New documentation-only branch name.")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Draft pull request title.")
    parser.add_argument(
        "--candidate",
        action="append",
        help="Explicit documentation candidate. Repeat exactly for the three allowlisted Markdown files.",
    )
    parser.add_argument("--execute", action="store_true", help="Permit live GitHub creation after all safeguards pass.")
    parser.add_argument("--operator-approval", help="Required exact explicit approval token when using --execute.")
    parser.add_argument("--output", type=Path, help="Optional local JSON receipt path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run a safe preview, or an explicitly approved documentation-only creation."""
    args = parse_args(argv)
    output_safe = False
    output_write_attempted = False
    try:
        _validate_output_path(args.output, args.docs_draft_dir, args.health_report, args.docs_evidence_path)
        _validate_branch_name(args.branch)
        if not REPOSITORY_FULL_NAME_RE.fullmatch(args.expected_upstream):
            raise DraftPrError("Expected upstream must be an owner/name repository identifier.")
        health_report = load_health_report(args.health_report, args.expected_upstream)
        docs_evidence_path = _read_docs_evidence(args.docs_evidence_path)
        _validate_output_against_checkout(args.output, health_report)
        output_safe = True
        documents, excluded = collect_documents(args.docs_draft_dir, args.candidate)
        if args.execute:
            receipt = create_draft_pr(args, documents, excluded, health_report, docs_evidence_path)
        else:
            receipt = build_preview(args, documents, excluded, health_report, docs_evidence_path)
        output_write_attempted = args.output is not None
        _write_receipt(receipt, args.output)
        return 0
    except (DraftPrError, OSError) as exc:
        error_receipt = {"mode": "live" if args.execute else "preview", "status": "halted", "error": str(exc)}
        _write_halted_receipt(error_receipt, args.output if output_safe and not output_write_attempted else None)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
