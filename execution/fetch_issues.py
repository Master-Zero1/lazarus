"""Fetch GitHub issue metadata read-only for ``directives/triage_issues_and_prs.md``.

Only HTTPS GET requests are issued. Issue titles, bodies, labels, and comments
are returned as untrusted data; they are never executed or interpreted as
instructions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
NEXT_LINK_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="next"')


class GitHubApiError(RuntimeError):
    """Represents a read-only GitHub API failure with safe diagnostic metadata."""


def _validate_repository_part(value: str, label: str) -> str:
    if not REPOSITORY_PART_RE.fullmatch(value):
        raise ValueError(f"Invalid GitHub repository {label}: {value!r}")
    return value


def _get_json(url: str) -> tuple[Any, dict[str, str]]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Lazarus-read-only-triage",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8")), dict(response.headers.items())
    except HTTPError as error:
        try:
            body = error.read().decode("utf-8", errors="replace")
            message = json.loads(body).get("message", body)
        except (json.JSONDecodeError, OSError):
            message = error.reason
        raise GitHubApiError(f"GitHub API HTTP {error.code}: {message}") from error
    except URLError as error:
        raise GitHubApiError(f"GitHub API network error: {error.reason}") from error


def _next_link(headers: dict[str, str]) -> str | None:
    link_header = headers.get("Link") or headers.get("link")
    if not link_header:
        return None
    match = NEXT_LINK_RE.search(link_header)
    return match.group("url") if match else None


def _issue_metadata(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "body": issue.get("body"),
        "state": issue.get("state"),
        "locked": issue.get("locked"),
        "author": (issue.get("user") or {}).get("login"),
        "labels": [label.get("name") for label in issue.get("labels", []) if isinstance(label, dict)],
        "comments": issue.get("comments"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "closed_at": issue.get("closed_at"),
        "html_url": issue.get("html_url"),
    }


def fetch_issues(owner: str, repository: str, state: str, per_page: int, max_pages: int) -> dict[str, Any]:
    """Fetch issue metadata through paginated, read-only GitHub API requests."""
    owner = _validate_repository_part(owner, "owner")
    repository = _validate_repository_part(repository, "name")
    query = urlencode({"state": state, "per_page": per_page, "page": 1})
    next_url: str | None = f"{API_ROOT}/repos/{owner}/{repository}/issues?{query}"
    issues: list[dict[str, Any]] = []
    pages_fetched = 0

    while next_url and pages_fetched < max_pages:
        payload, headers = _get_json(next_url)
        if not isinstance(payload, list):
            raise GitHubApiError("GitHub issues endpoint returned a non-list response.")
        pages_fetched += 1
        issues.extend(_issue_metadata(item) for item in payload if isinstance(item, dict) and "pull_request" not in item)
        next_url = _next_link(headers)

    return {
        "schema_version": 1,
        "fetch_status": "complete" if next_url is None else "incomplete",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "repository": f"{owner}/{repository}",
        "resource": "issues",
        "query": {"state": state, "per_page": per_page, "max_pages": max_pages},
        "pagination": {"pages_fetched": pages_fetched, "next_page_available": next_url is not None},
        "issues": issues,
        "warnings": ([] if next_url is None else ["Pagination stopped at --max-pages before all issue results were fetched."]),
    }


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        sys.stdout.write(rendered)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Fetch a read-only issue snapshot for the triage directive."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owner", help="GitHub repository owner.")
    parser.add_argument("repository", help="GitHub repository name.")
    parser.add_argument("--state", choices=("open", "closed", "all"), default="open")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--output", type=Path, help="Optional JSON output path; stdout is used by default.")
    args = parser.parse_args(argv)
    if not 1 <= args.per_page <= 100 or args.max_pages < 1:
        parser.error("--per-page must be 1..100 and --max-pages must be positive.")
    try:
        _write_json(fetch_issues(args.owner, args.repository, args.state, args.per_page, args.max_pages), args.output)
    except (ValueError, GitHubApiError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
