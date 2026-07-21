"""Assess dependency constraints from a manifest inventory without upgrading them.

Implements the freshness step in ``directives/diagnose_repo.md``.  It reads a
JSON inventory, applies only explicit offline lifecycle policy, and never
installs, resolves, upgrades, or otherwise changes dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


PYTHON_2_EOL = date(2020, 1, 1)
EXACT_SPECIFIER_RE = re.compile(r"^(?:===|==)\s*(?P<version>.+)$")
LOWER_BOUND_RE = re.compile(r"^>=\s*(?P<version>.+)$")
RANGE_CONSTRAINT_RE = re.compile(
    r"^(?:===|==|>=|<=|~=|!=|>|<)\s*[^,\s]+"
    r"(?:\s*,\s*(?:===|==|>=|<=|~=|!=|>|<)\s*[^,\s]+)+$"
)
NPM_RANGE_PREFIXES = ("^", "~")
NPM_NON_VERSION_PREFIXES = ("workspace:", "git+", "http", "file:")
NPM_NON_VERSION_VALUES = {"latest", "*"}


def _type_name(value: Any) -> str:
    """Return a stable, user-facing type name for malformed JSON evidence."""
    return type(value).__name__


def _validate_dependency(dependency: Any, label: str) -> None:
    """Reject malformed dependency evidence before assessment can dereference it."""
    if not isinstance(dependency, dict):
        raise ValueError(f"{label} must be a JSON object, got {_type_name(dependency)}.")

    for field in ("name", "normalized_name", "raw"):
        value = dependency.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{label}.{field} must be a string, got {_type_name(value)}.")

    if "specifier" in dependency and dependency["specifier"] is not None and not isinstance(dependency["specifier"], str):
        raise ValueError(f"{label}.specifier must be a string or null, got {_type_name(dependency['specifier'])}.")

    source = dependency.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"{label}.source must be a JSON object, got {_type_name(source)}.")
    for field in ("path", "kind"):
        value = source.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{label}.source.{field} must be a string, got {_type_name(value)}.")
    line = source.get("line")
    if not isinstance(line, int) or isinstance(line, bool):
        raise ValueError(f"{label}.source.line must be an integer, got {_type_name(line)}.")


def _validate_inventory_schema(inventory: dict[str, Any]) -> None:
    """Validate the inventory fields consumed by the offline assessment."""
    for field in ("conventional_manifests", "documented_runtime_requirements"):
        if field in inventory and not isinstance(inventory[field], list):
            raise ValueError(
                f"Manifest inventory field {field!r} must be a list when present, got {_type_name(inventory[field])}."
            )

    for index, dependency in enumerate(inventory.get("documented_runtime_requirements", [])):
        _validate_dependency(dependency, f"Manifest inventory documented_runtime_requirements[{index}]")

    for manifest_index, manifest in enumerate(inventory.get("conventional_manifests", [])):
        manifest_label = f"Manifest inventory conventional_manifests[{manifest_index}]"
        if not isinstance(manifest, dict):
            raise ValueError(f"{manifest_label} must be a JSON object, got {_type_name(manifest)}.")
        if "dependencies" in manifest and not isinstance(manifest["dependencies"], list):
            raise ValueError(
                f"{manifest_label}.dependencies must be a list when present, got {_type_name(manifest['dependencies'])}."
            )
        for dependency_index, dependency in enumerate(manifest.get("dependencies", [])):
            _validate_dependency(dependency, f"{manifest_label}.dependencies[{dependency_index}]")


def _all_dependencies(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies = list(inventory.get("documented_runtime_requirements", []))
    for manifest in inventory.get("conventional_manifests", []):
        dependencies.extend(manifest.get("dependencies", []))
    return dependencies


def _finding_for(dependency: dict[str, Any], as_of: date) -> dict[str, Any]:
    name = str(dependency["normalized_name"])
    specifier = dependency.get("specifier")
    traits: list[str] = []
    status = "unconstrained"
    severity = "info"
    reason = "No exact version constraint was declared."
    lifecycle: dict[str, str] | None = None

    specifier_text = str(specifier or "").strip()
    source_kind = str(dependency.get("source", {}).get("kind", ""))
    is_package_json_dependency = source_kind.startswith("package_json_")
    exact_match = EXACT_SPECIFIER_RE.match(specifier_text)
    lower_bound_match = LOWER_BOUND_RE.match(specifier_text)
    if is_package_json_dependency and specifier_text.startswith(NPM_RANGE_PREFIXES):
        traits.append("npm_range_constraint")
        status = "npm_range_constraint"
        reason = "The declaration uses an npm caret/tilde range; current compatibility is not established by this Python-oriented assessment."
    elif is_package_json_dependency and (
        specifier_text.startswith(NPM_NON_VERSION_PREFIXES) or specifier_text.lower() in NPM_NON_VERSION_VALUES
    ):
        traits.append("npm_non_version_specifier")
        status = "npm_non_version_specifier"
        reason = "The declaration is not a resolvable version specifier (workspace/git/URL/tag reference); version freshness cannot be assessed."
    elif exact_match:
        traits.append("exact_version_constraint")
        status = "pinned"
        reason = "The declaration specifies one exact version."
    elif RANGE_CONSTRAINT_RE.match(specifier or ""):
        traits.append("range_constraint")
        status = "bounded_range"
        reason = "The declaration specifies a version range; current compatibility is not established."
    elif lower_bound_match:
        traits.append("lower_bound_constraint")
        status = "legacy_baseline"
        reason = "The declaration sets only a minimum version; current compatibility is not established."

    if name == "python" and exact_match and exact_match.group("version").startswith("2.7"):
        traits.append("end_of_life")
        status = "obsolete"
        severity = "high"
        reason = "Python 2.7 reached end of life on 2020-01-01."
        lifecycle = {"end_of_life": PYTHON_2_EOL.isoformat(), "assessed_as_of": as_of.isoformat()}

    return {
        "dependency": dependency["name"],
        "normalized_name": name,
        "specifier": specifier,
        "traits": traits,
        "status": status,
        "severity": severity,
        "reason": reason,
        "lifecycle": lifecycle,
        "evidence": dependency["source"],
        "raw": dependency["raw"],
    }


def assess_freshness(inventory: dict[str, Any], as_of: date) -> dict[str, Any]:
    """Return a conservative offline freshness assessment for inventory entries."""
    findings = [_finding_for(dependency, as_of) for dependency in _all_dependencies(inventory)]
    findings.sort(key=lambda item: (item["normalized_name"], item["evidence"]["path"], item["evidence"]["line"]))
    return {
        "schema_version": 1,
        "assessment_mode": "offline_static_policy",
        "as_of": as_of.isoformat(),
        "inventory_repository": inventory.get("repository"),
        "findings": findings,
        "summary": {
            "dependencies_assessed": len(findings),
            "exact_version_constraints": sum("exact_version_constraint" in finding["traits"] for finding in findings),
            "obsolete": sum(finding["status"] == "obsolete" for finding in findings),
            "legacy_baselines": sum(finding["status"] == "legacy_baseline" for finding in findings),
            "bounded_ranges": sum(finding["status"] == "bounded_range" for finding in findings),
            "unconstrained": sum(finding["status"] == "unconstrained" for finding in findings),
            "npm_range_constraints": sum(finding["status"] == "npm_range_constraint" for finding in findings),
            "npm_non_version_specifiers": sum(finding["status"] == "npm_non_version_specifier" for finding in findings),
        },
        "limitations": [
            "This offline assessment does not query package indexes or validate runtime compatibility.",
            "A lower-bound-only declaration is not treated as an exact package pin.",
        ],
    }


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_output_path(inventory_path: Path, inventory: dict[str, Any], output_path: Path | None) -> None:
    """Prevent an assessment from replacing its inventory or target checkout."""
    if output_path is None:
        return
    if output_path.resolve() == inventory_path.resolve():
        raise ValueError(
            "Output path {0} collides with input manifest inventory {1}.".format(
                output_path.resolve(), inventory_path.resolve()
            )
        )
    repository = inventory.get("repository")
    if isinstance(repository, str) and repository.strip():
        repository_path = Path(repository)
        if repository_path.is_dir() and _is_within(output_path, repository_path):
            raise ValueError(
                "Output path {0} is inside the repository declared by manifest inventory {1}.".format(
                    output_path.resolve(), repository_path.resolve()
                )
            )


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        sys.stdout.write(rendered)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the freshness assessment named by ``directives/diagnose_repo.md``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory_path", type=Path, help="JSON inventory emitted by inventory_manifests.py.")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Assessment date in YYYY-MM-DD format.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path; stdout is used by default.")
    args = parser.parse_args(argv)
    try:
        as_of = date.fromisoformat(args.as_of)
        inventory = json.loads(args.inventory_path.read_text(encoding="utf-8"))
        if not isinstance(inventory, dict):
            raise ValueError(f"Manifest inventory must be a JSON object, got {_type_name(inventory)}.")
        _validate_inventory_schema(inventory)
        _validate_output_path(args.inventory_path, inventory, args.output)
        _write_json(assess_freshness(inventory, as_of), args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
