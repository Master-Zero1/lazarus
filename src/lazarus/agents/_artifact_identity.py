"""Shared repository-identity validation for Lazarus stage artifacts.

Local inventories and Health Reports identify a checkout by its repository
directory name. GitHub snapshots additionally identify an upstream as
``owner/repository``. A receipt may identify a fork and, when available, its
expected upstream. The validator always requires the repository name to agree
and requires all declared upstream identities to agree as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse


REMOTE_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
HEALTH_SCOPE_RE = re.compile(r"(?m)^Repository inspected: `([^`]+)`$")
HEALTH_UPSTREAM_RE = re.compile(r"(?m)^Repository canonical upstream: `([^`]+)`$")
TRIAGE_REPOSITORY_RE = re.compile(r"(?m)^- Repository: `([^`]+)`$")


class ArtifactIdentityError(ValueError):
    """An artifact is missing a repository declaration or conflicts with another."""


@dataclass(frozen=True)
class RepositoryIdentity:
    """A declared repository name plus its available verification strength."""

    name: str
    source: str
    declared: str
    upstream: str | None = None
    verification_level: str = "name_only"


def _repository_name(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1]
    if not name:
        raise ArtifactIdentityError("Repository declaration is empty.")
    return name


def _canonical_upstream(value: Any, source: str) -> str:
    """Validate one explicit canonical ``owner/repository`` identity."""
    if not isinstance(value, str) or REMOTE_REPOSITORY_RE.fullmatch(value.strip()) is None:
        raise ArtifactIdentityError(
            "{0} must declare a canonical owner/repository identity, got {1!r}.".format(source, value)
        )
    return value.strip()


def _github_upstream_from_remote_url(value: Any, source: str) -> str:
    """Extract one canonical GitHub owner/repository from an actual remote URL.

    The pipeline fetches backlog data from the public GitHub API, so matching
    only a local directory name is insufficient evidence that the cloned source
    and those GitHub snapshots refer to the same repository.  A full pipeline
    therefore accepts only an origin URL on github.com with exactly one
    owner/repository path pair.  The standalone clone utility remains able to
    clone other public Git hosts; this validator is the boundary for combining
    that checkout with GitHub API data.
    """

    if not isinstance(value, str) or not value.strip():
        raise ArtifactIdentityError("{0} does not declare an actual origin URL.".format(source))
    origin_url = value.strip()
    parsed = urlparse(origin_url)
    if parsed.scheme not in {"https", "git"} or parsed.hostname is None:
        raise ArtifactIdentityError(
            "{0} origin URL must be an https:// or git:// GitHub URL, got {1!r}.".format(
                source, origin_url
            )
        )
    if parsed.hostname.casefold() != "github.com":
        raise ArtifactIdentityError(
            "{0} origin URL must use github.com before Lazarus can combine it with GitHub API data, got {1!r}.".format(
                source, origin_url
            )
        )
    if parsed.username is not None or parsed.password is not None:
        raise ArtifactIdentityError("{0} origin URL must not contain credentials.".format(source))
    if parsed.params or parsed.query or parsed.fragment:
        raise ArtifactIdentityError(
            "{0} origin URL must not contain parameters, a query, or a fragment.".format(source)
        )

    parts = [part for part in unquote(parsed.path).strip("/").split("/") if part]
    if len(parts) != 2:
        raise ArtifactIdentityError(
            "{0} origin URL must identify exactly one GitHub owner/repository path, got {1!r}.".format(
                source, origin_url
            )
        )
    owner, repository = parts
    if repository.casefold().endswith(".git"):
        repository = repository[:-4]
    return _canonical_upstream("{0}/{1}".format(owner, repository), source + " origin URL")


def _identity(
    value: Any,
    source: str,
    *,
    require_remote: bool = False,
    expected_upstream: str | None = None,
) -> RepositoryIdentity:
    """Create an identity, binding local declarations when a canonical origin is known."""
    if not isinstance(value, str) or not value.strip():
        raise ArtifactIdentityError("{0} does not declare a repository identity.".format(source))
    declared = value.strip()
    is_remote = REMOTE_REPOSITORY_RE.fullmatch(declared) is not None
    if require_remote and not is_remote:
        raise ArtifactIdentityError("{0} must declare a canonical owner/repository identity, got {1!r}.".format(source, declared))
    supplied_upstream = _canonical_upstream(expected_upstream, source + " expected_upstream") if expected_upstream is not None else None
    declared_upstream = declared if is_remote else None
    if supplied_upstream is not None and declared_upstream is not None and supplied_upstream.casefold() != declared_upstream.casefold():
        raise ArtifactIdentityError(
            "Canonical upstream mismatch: {0}={1!r}; expected_upstream={2!r}.".format(
                source, declared_upstream, supplied_upstream
            )
        )
    upstream = supplied_upstream or declared_upstream
    if upstream is not None and _repository_name(declared).casefold() != _repository_name(upstream).casefold():
        raise ArtifactIdentityError(
            "Repository identity mismatch: {0}={1!r}; expected_upstream={2!r}.".format(source, declared, upstream)
        )
    return RepositoryIdentity(
        name=_repository_name(declared),
        source=source,
        declared=declared,
        upstream=upstream,
        verification_level="canonical" if upstream is not None else "name_only",
    )


def identity_from_local_path(
    path: Path,
    source: str,
    *,
    expected_upstream: str | None = None,
) -> RepositoryIdentity:
    """Create an identity for a local checkout, binding it when origin is known."""
    return _identity(str(path.resolve()), source, expected_upstream=expected_upstream)


def identity_from_clone_receipt(
    receipt: Mapping[str, Any],
    source: str,
    *,
    expected_upstream: str,
) -> RepositoryIdentity:
    """Bind an orchestrated checkout to its actual GitHub ``origin`` remote.

    ``clone_repo.py`` records ``origin_url`` by asking Git after a successful
    clone.  This makes an owner/repository match independent of a destination
    folder name or an operator-supplied URL string.
    """

    remote_upstream = _github_upstream_from_remote_url(receipt.get("origin_url"), source)
    return _identity(
        remote_upstream,
        source + " origin",
        require_remote=True,
        expected_upstream=expected_upstream,
    )


def identity_from_json(
    payload: Mapping[str, Any],
    source: str,
    field: str = "repository",
    *,
    require_remote: bool = False,
    expected_upstream: str | None = None,
) -> RepositoryIdentity:
    """Read the required identity field from a structured stage artifact."""
    return _identity(
        payload.get(field),
        source,
        require_remote=require_remote,
        expected_upstream=expected_upstream,
    )


def identity_from_health_report(
    content: str,
    source: str,
    *,
    expected_upstream: str | None = None,
) -> RepositoryIdentity:
    """Read Health Report scope and any persisted canonical upstream binding."""
    match = HEALTH_SCOPE_RE.search(content)
    if match is None:
        raise ArtifactIdentityError("{0} does not declare its inspected repository in the Scope section.".format(source))
    recorded = HEALTH_UPSTREAM_RE.search(content)
    recorded_upstream = recorded.group(1) if recorded is not None else None
    if expected_upstream is not None and recorded_upstream is not None:
        expected = _canonical_upstream(expected_upstream, source + " expected_upstream")
        declared = _canonical_upstream(recorded_upstream, source + " recorded canonical upstream")
        if expected.casefold() != declared.casefold():
            raise ArtifactIdentityError(
                "Canonical upstream mismatch: {0} records {1!r}; expected_upstream={2!r}.".format(
                    source, declared, expected
                )
            )
    return _identity(match.group(1), source, expected_upstream=expected_upstream or recorded_upstream)


def identity_from_triage_report(content: str, source: str) -> RepositoryIdentity:
    """Read the canonical repository declaration from a Backlog Triage Report."""
    match = TRIAGE_REPOSITORY_RE.search(content)
    if match is None:
        raise ArtifactIdentityError("{0} does not declare a repository in Scope and evidence.".format(source))
    return _identity(match.group(1), source, require_remote=True)


def identity_from_receipt(receipt: Mapping[str, Any], source: str) -> RepositoryIdentity | None:
    """Read a receipt identity, allowing an expected halt before target resolution."""
    if "target" not in receipt:
        if receipt.get("status") == "halted" and receipt.get("mode") in {"preview", "live"}:
            return None
        raise ArtifactIdentityError("{0} has no valid target object.".format(source))
    target = receipt.get("target")
    if not isinstance(target, Mapping):
        raise ArtifactIdentityError("{0} has no valid target object.".format(source))
    expected_upstream = target.get("expected_upstream")
    if isinstance(expected_upstream, str) and expected_upstream.strip():
        return _identity(expected_upstream, source + " expected_upstream", require_remote=True)
    fork_owner = target.get("fork_owner")
    fork_repo = target.get("fork_repo")
    if not isinstance(fork_owner, str) or not isinstance(fork_repo, str) or not fork_owner or not fork_repo:
        raise ArtifactIdentityError("{0} does not declare an expected upstream or complete fork identity.".format(source))
    # Legacy receipts may lack an expected upstream. Their fork name remains a
    # usable name-level identity, but it is not treated as a canonical parent.
    return RepositoryIdentity(name=fork_repo, source=source, declared="{0}/{1}".format(fork_owner, fork_repo))


def require_same_repository(*identities: RepositoryIdentity) -> RepositoryIdentity:
    """Reject blended artifacts and report whether the match is canonical or name-only."""
    if not identities:
        raise ArtifactIdentityError("No repository identities were supplied.")
    names = {identity.name.casefold() for identity in identities}
    if len(names) != 1:
        detail = "; ".join("{0}={1!r}".format(identity.source, identity.declared) for identity in identities)
        raise ArtifactIdentityError("Repository identity mismatch: {0}.".format(detail))
    upstreams = {identity.upstream.casefold() for identity in identities if identity.upstream is not None}
    if len(upstreams) > 1:
        detail = "; ".join("{0}={1!r}".format(identity.source, identity.upstream) for identity in identities if identity.upstream)
        raise ArtifactIdentityError("Canonical upstream mismatch: {0}.".format(detail))
    if not upstreams:
        verification_level = "name_only"
    elif all(identity.upstream is not None for identity in identities):
        verification_level = "canonical"
    else:
        verification_level = "mixed"
    return replace(identities[0], verification_level=verification_level)
