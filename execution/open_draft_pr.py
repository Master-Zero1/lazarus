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
    upstream_repository_name = expected_upstream.rsplit("/", 1)[1]
    if repository_name.casefold() != upstream_repository_name.casefold():
        raise DraftPrError(
            "Health Report target {0!r} does not match expected upstream repository {1!r}.".format(
                repository_name, upstream_repository_name
            )
        )
    return HealthReport(path=path.resolve(), repository_name=repository_name, constraints=_health_report_constraints(content))


def build_pr_body(health_report: HealthReport) -> str:
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

### Known legacy constraints carried forward

{constraints}

No application source code, dependency manifest, test, CI, license, or
configuration file was changed.
""".format(constraints=constraints)


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


def _write_receipt(receipt: dict[str, Any], output: Path | None) -> None:
    """Print a receipt and optionally persist the same structured result locally."""
    rendered = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)


def build_preview(
    args: argparse.Namespace,
    documents: list[DocumentationFile],
    excluded: list[str],
    health_report: HealthReport,
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
        "pull_request": {
            "title": args.title,
            "body": build_pr_body(health_report),
        },
        "candidate_files": [document.preview() for document in documents],
        "excluded_non_candidates": excluded,
        "safety_checks_before_remote_write": [
            "explicit --execute and exact operator approval token",
            "authenticated repository owner matches --fork-owner",
            "repository is a fork of {0}".format(args.expected_upstream),
            "authenticated caller has push permission",
            "base branch exists and is the repository default branch",
            "new branch does not already exist and has no open pull request",
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
            message = str(error_data.get("message", "GitHub rejected the request"))
        except (UnicodeDecodeError, json.JSONDecodeError):
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


def create_draft_pr(
    args: argparse.Namespace,
    documents: list[DocumentationFile],
    excluded: list[str],
    health_report: HealthReport,
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
    branch_endpoint = repo_endpoint + "/git/ref/heads/" + quote(args.branch, safe="/")
    if _get_or_none(branch_endpoint, token) is not None:
        raise DraftPrError("Branch {0!r} already exists; human review is required before reuse.".format(args.branch))

    pull_query = urlencode({"state": "open", "head": "{0}:{1}".format(args.fork_owner, args.branch), "per_page": 10})
    existing_pulls = _request_json("GET", repo_endpoint + "/pulls?" + pull_query, token)
    if not isinstance(existing_pulls, list):
        raise GitHubApiError(0, "Unexpected GitHub response while checking existing pull requests")
    if existing_pulls:
        raise DraftPrError("An open pull request already uses the proposed branch; human review is required.")

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
        "pull_request": {"title": args.title, "body": build_pr_body(health_report)},
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
                {"title": args.title, "head": args.branch, "base": args.base, "body": build_pr_body(health_report), "draft": True},
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
    try:
        _validate_branch_name(args.branch)
        if not REPOSITORY_FULL_NAME_RE.fullmatch(args.expected_upstream):
            raise DraftPrError("Expected upstream must be an owner/name repository identifier.")
        health_report = load_health_report(args.health_report, args.expected_upstream)
        documents, excluded = collect_documents(args.docs_draft_dir, args.candidate)
        if args.execute:
            receipt = create_draft_pr(args, documents, excluded, health_report)
        else:
            receipt = build_preview(args, documents, excluded, health_report)
        _write_receipt(receipt, args.output)
        return 0
    except DraftPrError as exc:
        error_receipt = {"mode": "live" if args.execute else "preview", "status": "halted", "error": str(exc)}
        _write_receipt(error_receipt, args.output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
