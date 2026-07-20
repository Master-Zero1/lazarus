"""Shared repository-identity validation for Lazarus stage artifacts.

Local inventories and Health Reports identify a checkout by its repository
directory name. GitHub snapshots additionally identify an upstream as
``owner/repository``. A receipt may identify a fork and, when available, its
expected upstream. The validator always requires the repository name to agree
and requires all declared upstream identities to agree as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


REMOTE_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
HEALTH_SCOPE_RE = re.compile(r"(?m)^Repository inspected: `([^`]+)`$")
TRIAGE_REPOSITORY_RE = re.compile(r"(?m)^- Repository: `([^`]+)`$")


class ArtifactIdentityError(ValueError):
    """An artifact is missing a repository declaration or conflicts with another."""


@dataclass(frozen=True)
class RepositoryIdentity:
    """A declared repository name plus an optional canonical upstream identity."""

    name: str
    source: str
    declared: str
    upstream: str | None = None


def _repository_name(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1]
    if not name:
        raise ArtifactIdentityError("Repository declaration is empty.")
    return name


def _identity(value: Any, source: str, *, require_remote: bool = False) -> RepositoryIdentity:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactIdentityError("{0} does not declare a repository identity.".format(source))
    declared = value.strip()
    is_remote = REMOTE_REPOSITORY_RE.fullmatch(declared) is not None
    if require_remote and not is_remote:
        raise ArtifactIdentityError("{0} must declare a canonical owner/repository identity, got {1!r}.".format(source, declared))
    return RepositoryIdentity(
        name=_repository_name(declared),
        source=source,
        declared=declared,
        upstream=declared if is_remote else None,
    )


def identity_from_local_path(path: Path, source: str) -> RepositoryIdentity:
    """Create an identity for an operator-provided local checkout path."""
    return _identity(str(path.resolve()), source)


def identity_from_json(
    payload: Mapping[str, Any],
    source: str,
    field: str = "repository",
    *,
    require_remote: bool = False,
) -> RepositoryIdentity:
    """Read the required identity field from a structured stage artifact."""
    return _identity(payload.get(field), source, require_remote=require_remote)


def identity_from_health_report(content: str, source: str) -> RepositoryIdentity:
    """Read the repository declaration from a Health Report scope section."""
    match = HEALTH_SCOPE_RE.search(content)
    if match is None:
        raise ArtifactIdentityError("{0} does not declare its inspected repository in the Scope section.".format(source))
    return _identity(match.group(1), source)


def identity_from_triage_report(content: str, source: str) -> RepositoryIdentity:
    """Read the canonical repository declaration from a Backlog Triage Report."""
    match = TRIAGE_REPOSITORY_RE.search(content)
    if match is None:
        raise ArtifactIdentityError("{0} does not declare a repository in Scope and evidence.".format(source))
    return _identity(match.group(1), source, require_remote=True)


def identity_from_receipt(receipt: Mapping[str, Any], source: str) -> RepositoryIdentity:
    """Read an identity from a PR receipt without inferring an unrecorded parent."""
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
    """Reject blended artifacts; require names and all declared upstreams to agree."""
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
    return identities[0]
