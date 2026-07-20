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

    exact_match = EXACT_SPECIFIER_RE.match(specifier or "")
    lower_bound_match = LOWER_BOUND_RE.match(specifier or "")
    if exact_match:
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
        },
        "limitations": [
            "This offline assessment does not query package indexes or validate runtime compatibility.",
            "A lower-bound-only declaration is not treated as an exact package pin.",
        ],
    }


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
        _write_json(assess_freshness(inventory, as_of), args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
