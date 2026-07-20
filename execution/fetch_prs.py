"""Fetch GitHub pull-request metadata read-only for the triage SOP.

Only HTTPS GET requests are issued. A requested PR detail lookup includes body,
comment counts, and GitHub's mergeability fields when the API makes them
available; no merge, comment, label, or branch operation is possible here.
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


def _list_metadata(pull_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pull_request.get("number"),
        "title": pull_request.get("title"),
        "body": pull_request.get("body"),
        "state": pull_request.get("state"),
        "draft": pull_request.get("draft"),
        "author": (pull_request.get("user") or {}).get("login"),
        "comments": pull_request.get("comments"),
        "review_comments": pull_request.get("review_comments"),
        "created_at": pull_request.get("created_at"),
        "updated_at": pull_request.get("updated_at"),
        "closed_at": pull_request.get("closed_at"),
        "merged_at": pull_request.get("merged_at"),
        "html_url": pull_request.get("html_url"),
    }


def _detail_metadata(pull_request: dict[str, Any]) -> dict[str, Any]:
    mergeable = pull_request.get("mergeable")
    mergeable_state = pull_request.get("mergeable_state")
    if mergeable is None:
        conflict_state = "unknown"
    elif mergeable is False or mergeable_state == "dirty":
        conflict_state = "conflicted"
    else:
        conflict_state = "not_conflicted"
    return {
        **_list_metadata(pull_request),
        "description": pull_request.get("body"),
        "merge_status": {
            "merged": pull_request.get("merged_at") is not None,
            "mergeable": mergeable,
            "mergeable_state": mergeable_state,
            "conflict_state": conflict_state,
        },
        "comment_counts": {
            "issue_comments": pull_request.get("comments"),
            "review_comments": pull_request.get("review_comments"),
        },
        "head": {"ref": (pull_request.get("head") or {}).get("ref"), "sha": (pull_request.get("head") or {}).get("sha")},
        "base": {"ref": (pull_request.get("base") or {}).get("ref"), "sha": (pull_request.get("base") or {}).get("sha")},
    }


def fetch_pull_requests(
    owner: str,
    repository: str,
    state: str,
    per_page: int,
    max_pages: int,
    detail_numbers: list[int],
) -> dict[str, Any]:
    """Fetch pull-request list metadata and optional detailed PR records via GET."""
    owner = _validate_repository_part(owner, "owner")
    repository = _validate_repository_part(repository, "name")
    query = urlencode({"state": state, "per_page": per_page, "page": 1})
    next_url: str | None = f"{API_ROOT}/repos/{owner}/{repository}/pulls?{query}"
    pull_requests: list[dict[str, Any]] = []
    pages_fetched = 0

    while next_url and pages_fetched < max_pages:
        payload, headers = _get_json(next_url)
        if not isinstance(payload, list):
            raise GitHubApiError("GitHub pull-requests endpoint returned a non-list response.")
        pages_fetched += 1
        pull_requests.extend(_list_metadata(item) for item in payload if isinstance(item, dict))
        next_url = _next_link(headers)

    details: list[dict[str, Any]] = []
    for number in sorted(set(detail_numbers)):
        if number < 1:
            raise ValueError("--detail-pr must be a positive pull-request number.")
        payload, _ = _get_json(f"{API_ROOT}/repos/{owner}/{repository}/pulls/{number}")
        if not isinstance(payload, dict):
            raise GitHubApiError(f"GitHub pull request #{number} returned a non-object response.")
        details.append(_detail_metadata(payload))

    return {
        "schema_version": 1,
        "fetch_status": "complete" if next_url is None else "incomplete",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "repository": f"{owner}/{repository}",
        "resource": "pull_requests",
        "query": {"state": state, "per_page": per_page, "max_pages": max_pages, "detail_numbers": sorted(set(detail_numbers))},
        "pagination": {"pages_fetched": pages_fetched, "next_page_available": next_url is not None},
        "pull_requests": pull_requests,
        "detailed_pull_requests": details,
        "warnings": ([] if next_url is None else ["Pagination stopped at --max-pages before all pull-request results were fetched."]),
    }


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        sys.stdout.write(rendered)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Fetch a read-only pull-request snapshot for the triage directive."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("owner", help="GitHub repository owner.")
    parser.add_argument("repository", help="GitHub repository name.")
    parser.add_argument("--state", choices=("open", "closed", "all"), default="open")
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--detail-pr", type=int, action="append", default=[], help="Pull-request number to fetch in detail; repeatable.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path; stdout is used by default.")
    args = parser.parse_args(argv)
    if not 1 <= args.per_page <= 100 or args.max_pages < 1:
        parser.error("--per-page must be 1..100 and --max-pages must be positive.")
    try:
        _write_json(fetch_pull_requests(args.owner, args.repository, args.state, args.per_page, args.max_pages, args.detail_pr), args.output)
    except (ValueError, GitHubApiError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
