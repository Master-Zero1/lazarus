"""Inventory CI configuration without executing workflows or repository code.

Implements the CI inventory step in ``directives/diagnose_repo.md``. The script
reads common CI configuration locations and emits a structured finding when no
configuration is present; absence is a diagnosis finding, not an error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT_CI_FILES = {
    ".travis.yml": "travis_ci",
    ".travis.yaml": "travis_ci",
    "appveyor.yml": "appveyor",
    "appveyor.yaml": "appveyor",
    "azure-pipelines.yml": "azure_pipelines",
    "azure-pipelines.yaml": "azure_pipelines",
    ".gitlab-ci.yml": "gitlab_ci",
    ".gitlab-ci.yaml": "gitlab_ci",
    "bitbucket-pipelines.yml": "bitbucket_pipelines",
    "bitbucket-pipelines.yaml": "bitbucket_pipelines",
    ".drone.yml": "drone",
    ".drone.yaml": "drone",
    ".cirrus.yml": "cirrus_ci",
    ".cirrus.yaml": "cirrus_ci",
    "jenkinsfile": "jenkins",
}
SEARCHED_LOCATIONS = [
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".travis.yml",
    ".travis.yaml",
    ".circleci/config.yml",
    ".circleci/config.yaml",
    "appveyor.yml",
    "azure-pipelines.yml",
    ".gitlab-ci.yml",
    "bitbucket-pipelines.yml",
    ".drone.yml",
    ".cirrus.yml",
    "Jenkinsfile",
]
SIMPLE_YAML_VALUE_RE = re.compile(r"^\s*(?P<key>name|runs-on|python-version|python)\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
TRAVIS_ORG_SHUTDOWN_DATE = "2021-05-31"
TRAVIS_ORG_SHUTDOWN_REFERENCE = "https://www.travis-ci.com/blog/2021-05-07-orgshutdown/"


def _relative(repo_path: Path, path: Path) -> str:
    return path.relative_to(repo_path).as_posix()


def _extract_metadata(path: Path) -> dict[str, list[str]]:
    """Extract simple scalar YAML values without interpreting the workflow."""
    metadata: dict[str, list[str]] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw_line.lstrip().startswith("#"):
            continue
        match = SIMPLE_YAML_VALUE_RE.match(raw_line)
        if not match:
            continue
        key = match.group("key").lower()
        value = match.group("value").split("#", 1)[0].strip().strip("'\"")
        if value:
            metadata.setdefault(key, []).append(value)
    return metadata


def _configuration(repo_path: Path, path: Path, provider: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "path": _relative(repo_path, path),
        "provider": provider,
        "parse_status": "metadata_extracted",
    }
    if path.suffix.lower() in {".yml", ".yaml"}:
        item["metadata"] = _extract_metadata(path)
    else:
        item["metadata"] = {}
        item["parse_status"] = "inventory_only"
    return item


def inventory_ci_configuration(repo_path: Path) -> dict[str, Any]:
    """Return a deterministic inventory of common CI configuration files."""
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_path}")

    configurations: list[dict[str, Any]] = []
    workflows_dir = repo_path / ".github" / "workflows"
    if workflows_dir.is_dir():
        workflow_files = sorted(
            [*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")],
            key=lambda path: path.name.lower(),
        )
        configurations.extend(_configuration(repo_path, path, "github_actions") for path in workflow_files if path.is_file())

    circle_config = repo_path / ".circleci" / "config.yml"
    circle_config_yaml = repo_path / ".circleci" / "config.yaml"
    for path in (circle_config, circle_config_yaml):
        if path.is_file():
            configurations.append(_configuration(repo_path, path, "circleci"))

    jenkinsfile_found = False
    for filename in ("Jenkinsfile", "jenkinsfile"):
        path = repo_path / filename
        if path.is_file():
            configurations.append(_configuration(repo_path, path, "jenkins"))
            jenkinsfile_found = True
            break

    for filename, provider in ROOT_CI_FILES.items():
        if filename == "jenkinsfile" and jenkinsfile_found:
            continue
        path = repo_path / filename
        if path.is_file():
            configurations.append(_configuration(repo_path, path, provider))

    configurations.sort(key=lambda item: item["path"])
    found = bool(configurations)
    findings: list[dict[str, Any]] = []
    if not found:
        findings.append(
            {
                "code": "no_ci_configuration_found",
                "status": "finding",
                "severity": "medium",
                "message": "No CI configuration found in supported repository locations.",
                "searched_locations": SEARCHED_LOCATIONS,
            }
        )
    for configuration in configurations:
        if configuration["provider"] != "travis_ci":
            continue
        findings.append(
            {
                "code": "travis_ci_endpoint_unverified",
                "status": "finding",
                "severity": "medium",
                "configuration_path": configuration["path"],
                "message": (
                    "A Travis CI configuration is present, but configuration presence does not establish a functioning current CI path. "
                    "The legacy travis-ci.org service stopped running builds after 2021-05-31; this static inventory cannot determine "
                    "whether the repository is enrolled on travis-ci.com or has a recent successful build."
                ),
                "legacy_service": "travis-ci.org",
                "legacy_service_stopped_builds_after": TRAVIS_ORG_SHUTDOWN_DATE,
                "reference_url": TRAVIS_ORG_SHUTDOWN_REFERENCE,
            }
        )

    return {
        "schema_version": 1,
        "repository": str(repo_path),
        "ci_configuration_found": found,
        "configurations": configurations,
        "findings": findings,
    }


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_output_path(repo_path: Path, output_path: Path | None) -> None:
    """Keep an optional CI inventory receipt outside the repository being read."""
    if output_path is not None and _is_within(output_path, repo_path):
        raise ValueError(
            "Output path {0} is inside the target repository {1}.".format(output_path.resolve(), repo_path.resolve())
        )


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        sys.stdout.write(rendered)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the CI inventory named by ``directives/diagnose_repo.md``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", type=Path, help="Path to the local repository clone to inspect.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path; stdout is used by default.")
    args = parser.parse_args(argv)
    try:
        _validate_output_path(args.repo_path, args.output)
        _write_json(inventory_ci_configuration(args.repo_path), args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
