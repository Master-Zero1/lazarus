"""Orchestrate the repository-diagnosis SOP into an evidence-backed Health Report.

This is Layer 2 orchestration for ``directives/diagnose_repo.md``. It invokes
the designated Layer 3 scripts in order and synthesizes only their JSON output;
it does not execute, modify, or otherwise operate inside the target repository.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

from ._artifact_identity import identity_from_json, identity_from_local_path, require_same_repository


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DIRECTIVE_PATH = PACKAGE_ROOT / "directives" / "diagnose_repo.md"
EXECUTION_DIR = PACKAGE_ROOT / "execution"
REQUIRED_EXECUTION_SCRIPTS = (
    "inventory_manifests.py",
    "check_dependency_freshness.py",
    "parse_ci_config.py",
    "inventory_code_structure.py",
)
MANIFEST_INVENTORY_REQUIRED_KEYS = {
    "repository",
    "conventional_manifests",
    "documented_runtime_requirements",
    "warnings",
}
FRESHNESS_REQUIRED_KEYS = {"inventory_repository", "findings", "summary"}
CI_INVENTORY_REQUIRED_KEYS = {
    "repository",
    "ci_configuration_found",
    "configurations",
    "findings",
}
TEST_STRUCTURE_INVENTORY_REQUIRED_KEYS = {"repository", "tests"}


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_directive() -> None:
    """Read the trusted SOP and ensure its required execution contract exists."""
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    missing = [script for script in REQUIRED_EXECUTION_SCRIPTS if script not in directive]
    if missing:
        raise RuntimeError(f"Diagnosis directive does not name required scripts: {', '.join(missing)}")


def _run_execution_script(script_name: str, arguments: list[str]) -> None:
    """Call one Layer 3 script and preserve its own error rather than guessing."""
    script_path = EXECUTION_DIR / script_name
    result = subprocess.run(
        [sys.executable, str(script_path), *arguments],
        cwd=PACKAGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "No output returned."
        raise RuntimeError(f"{script_name} failed with exit code {result.returncode}: {detail}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read execution output {path}: {error}") from error
    if not isinstance(loaded, dict):
        raise RuntimeError(f"Execution output {path} is not a JSON object.")
    return loaded


def _require_schema(payload: dict[str, Any], required_keys: set[str], label: str) -> None:
    """Reject incomplete Layer 3 artifacts before they can imply absence findings."""
    missing = sorted(required_keys - payload.keys())
    if missing:
        raise RuntimeError(f"{label} is missing required key(s): {', '.join(missing)}.")


def _require_list(value: Any, label: str) -> list[Any]:
    """Reject a malformed collection before rendering it as missing evidence."""
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be a list, got {type(value).__name__}.")
    return value


def _require_list_of_objects(payload: dict[str, Any], field: str, label: str) -> list[dict[str, Any]]:
    """Require the object records consumed by the Health Report renderer."""
    values = _require_list(payload[field], f"{label} field `{field}`")
    records: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise RuntimeError(
                f"{label} field `{field}` has malformed item at index {index}: expected an object, got {type(value).__name__}."
            )
        records.append(value)
    return records


def _require_object_when_present(record: dict[str, Any], field: str, label: str) -> None:
    """Keep evidence metadata object-shaped before report rendering calls ``.get``."""
    if field in record and not isinstance(record[field], dict):
        raise RuntimeError(
            f"{label} field `{field}` must be an object when present, got {type(record[field]).__name__}."
        )


def _validate_execution_payload_shapes(
    manifest_inventory: dict[str, Any],
    freshness: dict[str, Any],
    ci_inventory: dict[str, Any],
    test_structure_inventory: dict[str, Any],
) -> None:
    """Validate every collection whose records the report renderer dereferences."""
    _require_list_of_objects(manifest_inventory, "conventional_manifests", "Manifest inventory")
    documented_requirements = _require_list_of_objects(
        manifest_inventory, "documented_runtime_requirements", "Manifest inventory"
    )
    for index, dependency in enumerate(documented_requirements):
        _require_object_when_present(
            dependency, "source", f"Manifest inventory documented_runtime_requirements[{index}]"
        )
    _require_list(manifest_inventory["warnings"], "Manifest inventory field `warnings`")
    freshness_findings = _require_list_of_objects(freshness, "findings", "Dependency freshness")
    for index, finding in enumerate(freshness_findings):
        _require_object_when_present(finding, "evidence", f"Dependency freshness findings[{index}]")
    if not isinstance(ci_inventory["ci_configuration_found"], bool):
        raise RuntimeError(
            "CI inventory field `ci_configuration_found` must be a boolean, got "
            f"{type(ci_inventory['ci_configuration_found']).__name__}."
        )
    _require_list_of_objects(ci_inventory, "configurations", "CI inventory")
    _require_list_of_objects(ci_inventory, "findings", "CI inventory")
    tests = test_structure_inventory["tests"]
    if not isinstance(tests, dict):
        raise RuntimeError("Test structure inventory has malformed `tests` data: expected an object.")
    _require_list(tests.get("test_files"), "Test structure inventory field `tests.test_files`")
    _require_list(tests.get("test_directories"), "Test structure inventory field `tests.test_directories`")


def _quote(value: object) -> str:
    """Render evidence as inert inline text rather than executable instructions."""
    return str(value).replace("`", "'").replace("\r", " ").replace("\n", " ")


def _source_label(source: dict[str, Any]) -> str:
    return f"`{_quote(source.get('path', 'unknown'))}:{_quote(source.get('line', 'unknown'))}`"


def _render_observed_facts(
    manifest_inventory: dict[str, Any],
    freshness: dict[str, Any],
    ci_inventory: dict[str, Any],
    test_structure_inventory: dict[str, Any],
) -> list[str]:
    facts: list[str] = []
    manifests = manifest_inventory.get("conventional_manifests", [])
    if not manifests:
        facts.append("No conventional dependency manifest was found by the manifest inventory.")
    else:
        facts.append(f"The manifest inventory found {len(manifests)} conventional dependency manifest(s).")

    documented = manifest_inventory.get("documented_runtime_requirements", [])
    for dependency in documented:
        facts.append(
            f"Documentation declares `{_quote(dependency.get('name'))} {_quote(dependency.get('specifier') or '(no version)')}` "
            f"at {_source_label(dependency.get('source', {}))}."
        )

    for warning in manifest_inventory.get("warnings", []):
        facts.append(f"Manifest inventory warning: {_quote(warning)}")
    for manifest in manifests:
        if manifest.get("parse_status") != "parsed":
            facts.append(
                f"Manifest `{_quote(manifest.get('path', 'unknown'))}` ({_quote(manifest.get('kind', 'unknown'))}) was only partially parsed; "
                "some declared dependencies may be missing from this report."
            )

    for finding in freshness.get("findings", []):
        if finding.get("status") in {"obsolete", "legacy_baseline", "bounded_range", "pinned", "unconstrained"}:
            facts.append(
                f"Freshness assessment classifies `{_quote(finding.get('dependency'))} {_quote(finding.get('specifier') or '(no version)')}` "
                f"as **{_quote(finding.get('status'))}**: {_quote(finding.get('reason'))} "
                f"Evidence: {_source_label(finding.get('evidence', {}))}."
            )

    if ci_inventory.get("ci_configuration_found"):
        configurations = ci_inventory.get("configurations", [])
        facts.append(f"The CI inventory found {len(configurations)} configuration file(s).")
        for configuration in configurations:
            facts.append(
                f"CI configuration `{_quote(configuration.get('path', 'unknown'))}` declares provider `{_quote(configuration.get('provider', 'unknown'))}`."
            )
        if any(configuration.get("provider") != "travis_ci" for configuration in configurations):
            facts.append(
                "CI configuration presence does not establish that the configured pipeline currently runs successfully; only Travis has an automated "
                "legacy-lifecycle check in this version. Active status for other providers is not verified by this diagnosis."
            )
    else:
        facts.append("No CI configuration was found in the supported repository locations.")
    for finding in ci_inventory.get("findings", []):
        reference = finding.get("reference_url")
        suffix = f" Reference: {_quote(reference)}." if reference else ""
        facts.append(f"CI finding **{_quote(finding.get('code', 'unknown'))}**: {_quote(finding.get('message', ''))}{suffix}")

    tests = test_structure_inventory.get("tests")
    if not isinstance(tests, dict):
        raise RuntimeError("Test structure inventory has malformed `tests` data: expected an object.")
    test_files = tests.get("test_files")
    test_directories = tests.get("test_directories")
    if not isinstance(test_files, list) or not isinstance(test_directories, list):
        raise RuntimeError("Test structure inventory has malformed test-file or test-directory data: expected lists.")
    if test_files:
        facts.append(
            "Static test inventory detected {0} conventionally named test file(s).".format(len(test_files))
        )
    else:
        facts.append("Static test inventory detected no conventionally named test files.")
    if test_directories:
        facts.append(
            "Static test inventory detected test directory/directories: {0}.".format(
                ", ".join("`{0}`".format(_quote(path)) for path in test_directories)
            )
        )
    return facts


def _render_inferences(manifest_inventory: dict[str, Any], freshness: dict[str, Any], ci_inventory: dict[str, Any]) -> list[str]:
    inferences: list[str] = []
    if not manifest_inventory.get("conventional_manifests"):
        inferences.append("Environment reproduction is likely harder because runtime requirements are documented rather than encoded in a conventional manifest.")
    if any(finding.get("status") == "obsolete" for finding in freshness.get("findings", [])):
        inferences.append("The declared end-of-life runtime is likely a compatibility and maintenance risk; a maintainer decision is needed before modern validation can be planned.")
    if any(finding.get("status") in {"legacy_baseline", "bounded_range"} for finding in freshness.get("findings", [])):
        inferences.append("The declared dependency bounds leave supported current versions unclear, so compatibility cannot be assumed.")
    if not ci_inventory.get("ci_configuration_found"):
        inferences.append("The repository appears to lack repository-local automated verification configuration, increasing the effort needed to establish trustworthy revival checks.")
    if any(finding.get("code") == "travis_ci_endpoint_unverified" for finding in ci_inventory.get("findings", [])):
        inferences.append("The detected Travis configuration is evidence of declared CI intent, not evidence that current builds run; a maintainer must verify the active provider endpoint and a recent build result.")
    return inferences or ["The available deterministic evidence does not support additional diagnosis inferences."]


def _render_unknowns(manifest_inventory: dict[str, Any], freshness: dict[str, Any], ci_inventory: dict[str, Any]) -> list[str]:
    unknowns = [
        "Whether the declared runtime and dependency requirements still execute successfully; this diagnosis does not install or run them.",
        "Whether detected tests pass or cover the project adequately; this diagnosis inventories test-file presence only and does not execute tests.",
    ]
    if not manifest_inventory.get("conventional_manifests"):
        unknowns.append("Whether README-only requirements fully describe every runtime dependency.")
    if manifest_inventory.get("warnings") or any(
        manifest.get("parse_status") != "parsed" for manifest in manifest_inventory.get("conventional_manifests", [])
    ):
        unknowns.append("Whether the manifest scan captured every declared dependency; some manifest content could not be statically parsed or is recorded as a warning above.")
    if any(finding.get("status") in {"legacy_baseline", "bounded_range"} for finding in freshness.get("findings", [])):
        unknowns.append("Which current dependency versions, if any, remain compatible with the codebase.")
    if any(finding.get("status") == "unconstrained" for finding in freshness.get("findings", [])):
        unknowns.append("Which version(s) of the unconstrained dependencies (no declared version bound) are actually required; this cannot be established from the supplied evidence.")
    if not ci_inventory.get("ci_configuration_found"):
        unknowns.append("Whether CI exists outside the supported repository-local configuration locations.")
    if any(finding.get("code") == "travis_ci_endpoint_unverified" for finding in ci_inventory.get("findings", [])):
        unknowns.append("Whether the detected Travis configuration is enrolled on an active service endpoint or has run successfully recently.")
    return unknowns


def _render_blockers(manifest_inventory: dict[str, Any], freshness: dict[str, Any], ci_inventory: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    for finding in freshness.get("findings", []):
        if finding.get("status") == "obsolete":
            blockers.append(
                f"**Runtime lifecycle decision:** `{_quote(finding.get('dependency'))} {_quote(finding.get('specifier'))}` is obsolete; "
                "a maintainer must decide whether to preserve a legacy environment or plan a supported-runtime migration."
            )
    if not manifest_inventory.get("conventional_manifests"):
        blockers.append("**Reproducibility gap:** a maintainer must confirm and capture the complete supported dependency set before reliable setup validation.")
    if not ci_inventory.get("ci_configuration_found"):
        blockers.append("**Verification gap:** there is no repository-local CI configuration to demonstrate repeatable automated validation.")
    if any(finding.get("code") == "travis_ci_endpoint_unverified" for finding in ci_inventory.get("findings", [])):
        blockers.append("**CI service verification:** the Travis configuration must be checked against an active endpoint and a recent build before it is treated as functioning CI.")
    return blockers or ["No revival blockers were identified by the executed diagnosis evidence."]


def _render_priorities(manifest_inventory: dict[str, Any], freshness: dict[str, Any], ci_inventory: dict[str, Any]) -> list[str]:
    priorities: list[str] = []
    if any(finding.get("status") == "obsolete" for finding in freshness.get("findings", [])):
        priorities.append("Decide the supported Python/runtime policy and record whether legacy execution is still required.")
    if not manifest_inventory.get("conventional_manifests"):
        priorities.append("Confirm the README-declared dependencies against maintainers or a controlled environment, then create a reviewable dependency specification in a future authorized change.")
    if any(finding.get("status") in {"legacy_baseline", "bounded_range"} for finding in freshness.get("findings", [])):
        priorities.append("Define and test supported dependency version bounds before claiming current compatibility.")
    if not ci_inventory.get("ci_configuration_found"):
        priorities.append("Design a minimal CI validation plan after the supported runtime and dependency set are agreed.")
    if any(finding.get("code") == "travis_ci_endpoint_unverified" for finding in ci_inventory.get("findings", [])):
        priorities.append("Verify the Travis endpoint/enrollment and inspect a recent build result; preserve or replace the configuration only after a maintainer decision.")
    return priorities or ["Review the evidence bundle before choosing a revival workstream."]


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def synthesize_health_report(
    repo_path: Path,
    findings_dir: Path,
    manifest_inventory: dict[str, Any],
    freshness: dict[str, Any],
    ci_inventory: dict[str, Any],
    test_structure_inventory: dict[str, Any],
    expected_upstream: str | None = None,
    repository_label: str | None = None,
) -> str:
    """Render the Health Report sections required by the diagnosis directive."""
    _require_schema(manifest_inventory, MANIFEST_INVENTORY_REQUIRED_KEYS, "Manifest inventory")
    _require_schema(freshness, FRESHNESS_REQUIRED_KEYS, "Dependency freshness")
    _require_schema(ci_inventory, CI_INVENTORY_REQUIRED_KEYS, "CI inventory")
    _require_schema(test_structure_inventory, TEST_STRUCTURE_INVENTORY_REQUIRED_KEYS, "Test structure inventory")
    _validate_execution_payload_shapes(manifest_inventory, freshness, ci_inventory, test_structure_inventory)
    evidence_paths = {
        "Manifest inventory": findings_dir / "manifest_inventory.json",
        "Dependency freshness": findings_dir / "dependency_freshness.json",
        "CI inventory": findings_dir / "ci_inventory.json",
        "Static test inventory": findings_dir / "test_structure_inventory.json",
    }
    blockers = _render_blockers(manifest_inventory, freshness, ci_inventory)
    return "\n".join(
        [
            "# Health Report",
            "",
            "## Scope",
            "",
            f"Repository inspected: `{_quote(repo_path.resolve())}`",
            *([f"Repository canonical upstream: `{_quote(expected_upstream)}`"] if expected_upstream else []),
            *([f"Operator-supplied repository label: `{_quote(repository_label)}`"] if repository_label else []),
            "",
            "This report is synthesized from the deterministic diagnosis scripts. It does not execute repository code, install dependencies, modify the clone, or contact remote services.",
            "",
            "## Evidence Bundle",
            "",
            _bullets([f"{label}: `{_quote(path)}`" for label, path in evidence_paths.items()]),
            "",
            "## Observed Facts",
            "",
            _bullets(_render_observed_facts(manifest_inventory, freshness, ci_inventory, test_structure_inventory)),
            "",
            "## Reasonable Inferences",
            "",
            _bullets(_render_inferences(manifest_inventory, freshness, ci_inventory)),
            "",
            "## Unknowns and Limits",
            "",
            _bullets(_render_unknowns(manifest_inventory, freshness, ci_inventory)),
            "",
            "## Revival Blockers",
            "",
            _bullets(blockers),
            "",
            "## Human Review Priorities",
            "",
            _bullets(_render_priorities(manifest_inventory, freshness, ci_inventory)),
            "",
        ]
    )


def diagnose(
    repo_path: Path,
    findings_dir: Path,
    as_of: date,
    expected_upstream: str | None = None,
    repository_label: str | None = None,
) -> str:
    """Run the directive's four read-only scripts in order and return the Health Report."""
    repo_path = repo_path.resolve()
    findings_dir = findings_dir.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_path}")
    if _is_within(findings_dir, repo_path):
        raise ValueError("Findings directory must be outside the target repository.")

    _validate_directive()
    findings_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = findings_dir / "manifest_inventory.json"
    freshness_path = findings_dir / "dependency_freshness.json"
    ci_path = findings_dir / "ci_inventory.json"
    test_structure_path = findings_dir / "test_structure_inventory.json"

    _run_execution_script("inventory_manifests.py", [str(repo_path), "--output", str(manifest_path)])
    _run_execution_script(
        "check_dependency_freshness.py",
        [str(manifest_path), "--as-of", as_of.isoformat(), "--output", str(freshness_path)],
    )
    _run_execution_script("parse_ci_config.py", [str(repo_path), "--output", str(ci_path)])
    _run_execution_script("inventory_code_structure.py", [str(repo_path), "--output", str(test_structure_path)])

    manifest_inventory = _read_json(manifest_path)
    _require_schema(manifest_inventory, MANIFEST_INVENTORY_REQUIRED_KEYS, "Manifest inventory")
    freshness = _read_json(freshness_path)
    _require_schema(freshness, FRESHNESS_REQUIRED_KEYS, "Dependency freshness")
    ci_inventory = _read_json(ci_path)
    _require_schema(ci_inventory, CI_INVENTORY_REQUIRED_KEYS, "CI inventory")
    test_structure_inventory = _read_json(test_structure_path)
    _require_schema(test_structure_inventory, TEST_STRUCTURE_INVENTORY_REQUIRED_KEYS, "Test structure inventory")
    require_same_repository(
        identity_from_local_path(repo_path, "diagnosis target", expected_upstream=expected_upstream),
        identity_from_json(manifest_inventory, "manifest inventory", expected_upstream=expected_upstream),
        identity_from_json(
            freshness,
            "dependency freshness",
            field="inventory_repository",
            expected_upstream=expected_upstream,
        ),
        identity_from_json(ci_inventory, "CI inventory", expected_upstream=expected_upstream),
        identity_from_json(test_structure_inventory, "test structure inventory", expected_upstream=expected_upstream),
    )

    return synthesize_health_report(
        repo_path,
        findings_dir,
        manifest_inventory,
        freshness,
        ci_inventory,
        test_structure_inventory,
        expected_upstream,
        repository_label,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the Layer 2 diagnosis agent for a local operator-controlled clone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", type=Path, help="Path to the local repository clone to diagnose.")
    parser.add_argument("--findings-dir", type=Path, help="Directory outside the clone for deterministic script outputs.")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Freshness assessment date in YYYY-MM-DD format.")
    parser.add_argument("--expected-upstream", help="Optional known canonical owner/repository identity for this checkout.")
    parser.add_argument("--repo-owner", help="Optional operator-supplied owner label; used only with --repo-name in the Health Report scope.")
    parser.add_argument("--repo-name", help="Optional operator-supplied repository label; used only with --repo-owner in the Health Report scope.")
    parser.add_argument("--output", type=Path, help="Optional Markdown report path outside the clone; stdout is used by default.")
    args = parser.parse_args(argv)

    repo_path = args.repo_path.resolve()
    findings_dir = args.findings_dir or (Path.cwd() / ".tmp" / f"{repo_path.name}_diagnosis")
    output_path = args.output.resolve() if args.output else None
    try:
        as_of = date.fromisoformat(args.as_of)
        if bool(args.repo_owner) != bool(args.repo_name):
            raise ValueError("--repo-owner and --repo-name must be supplied together when using a cosmetic repository label.")
        if output_path and _is_within(output_path, repo_path):
            raise ValueError("Report output path must be outside the target repository.")
        repository_label = "{0}/{1}".format(args.repo_owner, args.repo_name) if args.repo_owner else None
        report = diagnose(repo_path, findings_dir, as_of, args.expected_upstream, repository_label)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report, encoding="utf-8")
        else:
            sys.stdout.write(report)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
