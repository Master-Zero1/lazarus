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

try:
    from ._artifact_identity import identity_from_json, identity_from_local_path, require_same_repository
except ImportError:  # pragma: no cover - direct CLI execution.
    from _artifact_identity import identity_from_json, identity_from_local_path, require_same_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIRECTIVE_PATH = REPOSITORY_ROOT / "directives" / "diagnose_repo.md"
EXECUTION_DIR = REPOSITORY_ROOT / "execution"
REQUIRED_EXECUTION_SCRIPTS = (
    "inventory_manifests.py",
    "check_dependency_freshness.py",
    "parse_ci_config.py",
)


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
        cwd=REPOSITORY_ROOT,
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


def _quote(value: object) -> str:
    """Render evidence as inert inline text rather than executable instructions."""
    return str(value).replace("`", "'").replace("\r", " ").replace("\n", " ")


def _source_label(source: dict[str, Any]) -> str:
    return f"`{_quote(source.get('path', 'unknown'))}:{_quote(source.get('line', 'unknown'))}`"


def _render_observed_facts(
    manifest_inventory: dict[str, Any],
    freshness: dict[str, Any],
    ci_inventory: dict[str, Any],
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

    for finding in freshness.get("findings", []):
        if finding.get("status") in {"obsolete", "legacy_baseline", "bounded_range", "pinned"}:
            facts.append(
                f"Freshness assessment classifies `{_quote(finding.get('dependency'))} {_quote(finding.get('specifier') or '(no version)')}` "
                f"as **{_quote(finding.get('status'))}**: {_quote(finding.get('reason'))} "
                f"Evidence: {_source_label(finding.get('evidence', {}))}."
            )

    if ci_inventory.get("ci_configuration_found"):
        facts.append(f"The CI inventory found {len(ci_inventory.get('configurations', []))} configuration file(s).")
        for configuration in ci_inventory.get("configurations", []):
            facts.append(
                f"CI configuration `{_quote(configuration.get('path', 'unknown'))}` declares provider `{_quote(configuration.get('provider', 'unknown'))}`."
            )
    else:
        facts.append("No CI configuration was found in the supported repository locations.")
    for finding in ci_inventory.get("findings", []):
        reference = finding.get("reference_url")
        suffix = f" Reference: {_quote(reference)}." if reference else ""
        facts.append(f"CI finding **{_quote(finding.get('code', 'unknown'))}**: {_quote(finding.get('message', ''))}{suffix}")
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
        "Whether tests exist, pass, or cover the project adequately; test-suite inventory is outside the three executed diagnosis scripts.",
    ]
    if not manifest_inventory.get("conventional_manifests"):
        unknowns.append("Whether README-only requirements fully describe every runtime dependency.")
    if any(finding.get("status") in {"legacy_baseline", "bounded_range"} for finding in freshness.get("findings", [])):
        unknowns.append("Which current dependency versions, if any, remain compatible with the codebase.")
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
) -> str:
    """Render the Health Report sections required by the diagnosis directive."""
    evidence_paths = {
        "Manifest inventory": findings_dir / "manifest_inventory.json",
        "Dependency freshness": findings_dir / "dependency_freshness.json",
        "CI inventory": findings_dir / "ci_inventory.json",
    }
    blockers = _render_blockers(manifest_inventory, freshness, ci_inventory)
    return "\n".join(
        [
            "# Health Report",
            "",
            "## Scope",
            "",
            f"Repository inspected: `{_quote(repo_path.resolve())}`",
            "",
            "This report is synthesized from the deterministic diagnosis scripts. It does not execute repository code, install dependencies, modify the clone, or contact remote services.",
            "",
            "## Evidence Bundle",
            "",
            _bullets([f"{label}: `{_quote(path)}`" for label, path in evidence_paths.items()]),
            "",
            "## Observed Facts",
            "",
            _bullets(_render_observed_facts(manifest_inventory, freshness, ci_inventory)),
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


def diagnose(repo_path: Path, findings_dir: Path, as_of: date) -> str:
    """Run the directive's three scripts in order and return the Health Report."""
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

    _run_execution_script("inventory_manifests.py", [str(repo_path), "--output", str(manifest_path)])
    _run_execution_script(
        "check_dependency_freshness.py",
        [str(manifest_path), "--as-of", as_of.isoformat(), "--output", str(freshness_path)],
    )
    _run_execution_script("parse_ci_config.py", [str(repo_path), "--output", str(ci_path)])

    manifest_inventory = _read_json(manifest_path)
    freshness = _read_json(freshness_path)
    ci_inventory = _read_json(ci_path)
    require_same_repository(
        identity_from_local_path(repo_path, "diagnosis target"),
        identity_from_json(manifest_inventory, "manifest inventory"),
        identity_from_json(freshness, "dependency freshness", field="inventory_repository"),
        identity_from_json(ci_inventory, "CI inventory"),
    )

    return synthesize_health_report(
        repo_path,
        findings_dir,
        manifest_inventory,
        freshness,
        ci_inventory,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the Layer 2 diagnosis agent for a local operator-controlled clone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", type=Path, help="Path to the local repository clone to diagnose.")
    parser.add_argument("--findings-dir", type=Path, help="Directory outside the clone for deterministic script outputs.")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Freshness assessment date in YYYY-MM-DD format.")
    parser.add_argument("--output", type=Path, help="Optional Markdown report path outside the clone; stdout is used by default.")
    args = parser.parse_args(argv)

    repo_path = args.repo_path.resolve()
    findings_dir = args.findings_dir or (REPOSITORY_ROOT / ".tmp" / f"{repo_path.name}_diagnosis")
    output_path = args.output.resolve() if args.output else None
    try:
        as_of = date.fromisoformat(args.as_of)
        if output_path and _is_within(output_path, repo_path):
            raise ValueError("Report output path must be outside the target repository.")
        report = diagnose(repo_path, findings_dir, as_of)
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
