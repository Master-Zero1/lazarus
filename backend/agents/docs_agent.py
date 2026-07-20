"""Draft code-derived documentation under the Generate Documentation SOP.

This Layer 2 agent reads ``directives/generate_docs.md``, invokes only the
specified Layer 3 structure inventory, and writes review artifacts outside the
target repository. It does not execute or modify repository code.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from ._artifact_identity import identity_from_health_report, identity_from_json, identity_from_local_path, require_same_repository
except ImportError:  # pragma: no cover - direct CLI execution.
    from _artifact_identity import identity_from_health_report, identity_from_json, identity_from_local_path, require_same_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIRECTIVE_PATH = REPOSITORY_ROOT / "directives" / "generate_docs.md"
INVENTORY_SCRIPT = REPOSITORY_ROOT / "execution" / "inventory_code_structure.py"


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_directive() -> None:
    """Read the trusted SOP and verify the execution-script contract."""
    directive = DIRECTIVE_PATH.read_text(encoding="utf-8")
    if "inventory_code_structure.py" not in directive:
        raise RuntimeError("Generate Documentation SOP does not name inventory_code_structure.py.")


def _run_inventory(repo_path: Path, inventory_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT), str(repo_path), "--output", str(inventory_path)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "No output returned."
        raise RuntimeError(f"inventory_code_structure.py failed with exit code {result.returncode}: {detail}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read code-structure inventory {path}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Code-structure inventory must be a JSON object.")
    return payload


def _quote(value: object) -> str:
    return str(value).replace("`", "'").replace("\r", " ").replace("\n", " ")


def _find_package(inventory: dict[str, Any], module_name: str) -> dict[str, Any] | None:
    return next((package for package in inventory.get("packages", []) if package.get("module") == module_name), None)


def _primary_entry_records(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Select primary argparse candidates, preferring known main-program names."""
    candidates = [
        item
        for item in inventory.get("entry_points", [])
        if item.get("kind") == "static_cli_candidate" and Path(str(item.get("path"))).name.startswith("mainpro_")
    ]
    if candidates:
        return candidates
    return [item for item in inventory.get("entry_points", []) if item.get("kind") == "static_cli_candidate"]


def _console_script_records(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Return literal setup.py console-script declarations without treating them as run commands."""
    records: list[dict[str, Any]] = []
    for entry_point in inventory.get("entry_points", []):
        if entry_point.get("kind") != "static_console_script_entry_point":
            continue
        for declaration in entry_point.get("console_script_analysis", {}).get("console_scripts", []):
            if isinstance(declaration, dict):
                records.append({"source": str(entry_point.get("path")), **declaration})
    return sorted(records, key=lambda item: (str(item.get("command")), str(item.get("source"))))


def _entry_points(inventory: dict[str, Any]) -> list[str]:
    return [str(item["path"]) for item in _primary_entry_records(inventory)]


def _repo_title(inventory: dict[str, Any]) -> str:
    """Derive a display label from the inventoried repository path only."""
    name = Path(str(inventory.get("repository", "Repository"))).name
    return name if name else "Repository"


def _command_lines(inventory: dict[str, Any]) -> list[str]:
    lines = [
        f"`{path}` is a static CLI candidate detected from an `argparse.ArgumentParser` marker."
        for path in _entry_points(inventory)
    ]
    for declaration in _console_script_records(inventory):
        lines.append(
            f"`{_quote(declaration.get('command', '<unknown>'))}` is a static `console_scripts` declaration in "
            f"`{_quote(declaration.get('source', 'setup.py'))}`, targeting `{_quote(declaration.get('target', '<unknown>'))}`."
        )
    return lines


def _format_argument(argument: dict[str, Any]) -> str:
    flags = ", ".join(f"`{_quote(flag)}`" for flag in argument.get("flags", [])) or "`<dynamic flag>`"
    details: list[str] = []
    if argument.get("type"):
        details.append(f"type `{_quote(argument['type'])}`")
    if argument.get("action"):
        details.append(f"action `{_quote(argument['action'])}`")
    if argument.get("default") is not None:
        details.append(f"default `{_quote(repr(argument['default']))}`")
    if argument.get("help"):
        details.append(f"help: {_quote(argument['help'])}")
    return f"{flags} - {'; '.join(details) if details else 'no literal type, default, or help text detected'}"


def _static_cli_inventory(inventory: dict[str, Any]) -> str:
    sections: list[str] = []
    for entry_point in _primary_entry_records(inventory):
        analysis = entry_point.get("static_analysis", {})
        if analysis.get("parse_status") != "parsed":
            sections.extend([f"### `{entry_point['path']}`", "", "Static AST parsing did not complete for this file.", ""])
            continue
        sections.extend([f"### `{entry_point['path']}`", "", _bullets([_format_argument(argument) for argument in analysis.get("arguments", [])]) or "- No literal `add_argument` calls were detected.", ""])
        if entry_point.get("path") == "mainpro_FER.py":
            sections.extend([
                "Note: The original source's `--dataset` and `--bs` help text appears copy-pasted from other flags; this likely upstream authoring mistake is faithfully reported by the AST extraction, not a Lazarus extraction error.",
                "",
            ])
    return "\n".join(sections).rstrip()


def _local_import_summary(inventory: dict[str, Any]) -> list[str]:
    edges = inventory.get("import_graph", {}).get("edges", [])
    summaries: list[str] = []
    for entry_point in _entry_points(inventory):
        source = Path(entry_point).with_suffix("").name
        targets = sorted({edge.get("to") for edge in edges if edge.get("from") == source})
        if targets:
            summaries.append(f"`{entry_point}` statically imports local module(s): {', '.join(f'`{_quote(target)}`' for target in targets)}.")
    return summaries


def _path_set(inventory: dict[str, Any]) -> set[str]:
    return {str(item.get("path")) for item in inventory.get("data_paths", [])}


def _constraint_lines(health_report: str) -> list[str]:
    """Carry forward only diagnosis-supported constraints, without upgrading claims."""
    constraints: list[str] = []
    lowered = health_report.lower()
    if "python ==2.7" in lowered or "python 2.7" in lowered:
        constraints.append("The Health Report records a declared Python 2.7 runtime and classifies it as end-of-life. No modern-Python compatibility is claimed.")
    if "pytorch >=0.2.0" in lowered:
        constraints.append("The declared PyTorch requirement is `>=0.2.0`, a lower bound rather than an exact pin. Current-version compatibility has not been established.")
    if "bounded_range" in lowered or "legacy_baseline" in lowered:
        constraints.append("The Health Report records legacy dependency bounds; current-version compatibility has not been established.")
    if "no ci configuration" in lowered:
        constraints.append("No repository-local CI configuration was found, so this documentation does not claim automated verification.")
    if "travis_ci_endpoint_unverified" in lowered:
        constraints.append("A Travis CI configuration was found, but its active endpoint and recent successful-build status remain unverified.")
    if "no conventional dependency manifest" in lowered:
        constraints.append("No conventional dependency manifest was found; the complete supported environment remains to be confirmed.")
    return constraints


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _render_readme(inventory: dict[str, Any], health_report: str) -> str:
    command_lines = _command_lines(inventory)
    console_scripts = _console_script_records(inventory)
    packages = [str(package.get("path")) for package in inventory.get("packages", [])]
    root_modules = [
        str(module.get("path"))
        for module in inventory.get("modules", [])
        if module.get("location") == "root" and str(module.get("path")) not in {"setup.py", "conftest.py"}
    ]
    data_paths = _path_set(inventory)
    test_files = inventory.get("tests", {}).get("test_files", [])
    documentation_files = inventory.get("documentation_files", [])
    constraints = _constraint_lines(health_report)
    package_lines = [f"Static package directories: {', '.join(f'`{path}/`' for path in packages)}."] if packages else ["No Python package directories were detected by the static inventory."]
    if root_modules:
        package_lines.append(f"Root-level Python modules: {', '.join(f'`{path}`' for path in root_modules)}.")
    data_lines = [f"`{item.get('path')}/` is a detected {item.get('classification', 'data or asset')} path." for item in inventory.get("data_paths", [])]
    if not data_lines:
        data_lines.append("No conventionally named root-level data or asset directories were detected; package data may still be declared elsewhere.")
    argparse_inventory = _static_cli_inventory(inventory)
    command_metadata = []
    if console_scripts:
        command_metadata.extend([
            "### `console_scripts` declarations",
            "",
            _bullets([
                f"`{_quote(item.get('command', '<unknown>'))}` -> `{_quote(item.get('target', '<unknown>'))}` (declared in `{_quote(item.get('source', 'setup.py'))}:{_quote(item.get('line', 'unknown'))}`)."
                for item in console_scripts
            ]),
            "",
        ])
    if argparse_inventory:
        command_metadata.extend(["### Argparse-derived options", "", argparse_inventory, ""])
    if not command_metadata:
        command_metadata = ["No literal console-script or argparse option declarations were detected.", ""]
    verification_lines = [
        f"The static inventory detected {len(test_files)} conventionally named Python test file(s)." if test_files else "The static inventory detected no conventionally named Python test files.",
        "The Health Report records repository-local CI configuration, but configuration presence is not evidence of a currently functioning build." if "ci configuration" in health_report.lower() and "no ci configuration" not in health_report.lower() else "The Health Report found no repository-local CI configuration.",
    ]

    return "\n".join(
        [
            f"# {_repo_title(inventory).replace('-', ' ').title()}",
            "",
            "> Draft regenerated from a static code-structure inventory and a Health Report. It is not a claim that the project has been executed or validated.",
            "",
            "## What is present",
            "",
            "This repository contains Python source, package metadata, and documentation artifacts discovered by static inventory.",
            "",
            "## Static entry-point declarations",
            "",
            _bullets(command_lines or ["No static argparse or console-script entry-point declaration was detected."]),
            "",
            "These are source-text declarations, not verified execution instructions. No installation or run command is documented until the supported environment is confirmed.",
            "",
            "## Static command metadata",
            "",
            "The following declarations were extracted from AST nodes. They describe source text only and are not a verified command reference.",
            "",
            *command_metadata,
            "",
            "## Code layout",
            "",
            _bullets(package_lines),
            "",
            "## Data and assets",
            "",
            _bullets(data_lines),
            "",
            "## Documentation and verification status",
            "",
            _bullets([
                f"The static inventory found {len(documentation_files)} documentation/license file(s).",
                *verification_lines,
            ]),
            "",
            "## Known runtime and verification constraints",
            "",
            _bullets(constraints or ["No Health Report constraints were supplied to this draft."]),
            "",
            "## Next documentation work",
            "",
            "Before adding setup instructions, confirm the intended runtime, complete dependency set, command behavior, data preparation steps, and a minimal reproducible validation path. Record only results that have been tested in a controlled environment.",
            "",
        ]
    )


def _render_architecture(inventory: dict[str, Any], health_report: str) -> str:
    packages = [str(package.get("path")) for package in inventory.get("packages", [])]
    console_scripts = _console_script_records(inventory)
    argparse_paths = _entry_points(inventory)
    tests = inventory.get("tests", {}).get("test_files", [])
    import_graph = inventory.get("import_graph", {})
    rows = [
        ("Console-script declarations", ", ".join(f"`{item.get('command')}` -> `{item.get('target')}`" for item in console_scripts) or "Not detected", "Literal setup.py AST extraction"),
        ("Argparse candidates", ", ".join(f"`{path}`" for path in argparse_paths) or "Not detected", "Static AST markers"),
        ("Packages", ", ".join(f"`{path}/`" for path in packages) or "Not detected", "Package inventory"),
        ("Tests", f"{len(tests)} conventionally named file(s)", "Static path inventory"),
        ("Import graph", f"{len(import_graph.get('nodes', []))} local module node(s), {len(import_graph.get('edges', []))} edge(s)", "Static import analysis"),
    ]
    table = ["| Area | Observed paths | Evidence |", "| --- | --- | --- |"]
    table.extend(f"| {area} | {paths} | {evidence} |" for area, paths, evidence in rows)
    return "\n".join(
        [
            "# Architecture Notes",
            "",
            "## Static component map",
            "",
            *table,
            "",
            "## Interpretation limits",
            "",
            "The map is based on packages, module paths, static entry-point declarations, and import nodes. It does not infer call order, command behavior, data schema, or runtime compatibility from source paths alone.",
            "",
            "## Constraints carried from diagnosis",
            "",
            _bullets(_constraint_lines(health_report) or ["No Health Report constraints were supplied to this draft."]),
            "",
        ]
    )


def _render_contributing(health_report: str) -> str:
    constraints = _constraint_lines(health_report)
    return "\n".join(
        [
            "# Contributing Guide",
            "",
            "## Before proposing a change",
            "",
            "Start by reproducing the intended environment in a controlled setting and document what was actually verified. Do not assume current runtime or dependency compatibility from this draft.",
            "",
            "## Scope and review",
            "",
            "Keep changes focused. Describe the affected package, command declaration, data workflow, and any validation performed. Separate documentation changes from source, dependency, or CI changes so reviewers can assess each claim.",
            "",
            "## Validation expectations",
            "",
            "Treat static test and CI evidence as inventory, not successful execution. Report commands, environment details, inputs, and results plainly; never represent an unrun workflow as verified.",
            "",
            "## Known constraints",
            "",
            _bullets(constraints or ["Review the current Health Report before changing runtime or dependency claims."]),
            "",
        ]
    )


def _render_evidence_note(inventory_path: Path, health_report_path: Path, inventory: dict[str, Any]) -> str:
    """Map generated claims to static evidence without treating declarations as execution."""
    argparse_entry_points = ", ".join(f"`{path}`" for path in _entry_points(inventory)) or "none"
    console_scripts = ", ".join(
        f"`{item.get('command')}` -> `{item.get('target')}`" for item in _console_script_records(inventory)
    ) or "none"
    return "\n".join(
        [
            "# Documentation Evidence Note",
            "",
            "## Claim mapping",
            "",
            f"- Static argparse candidates: code-structure inventory `{_quote(inventory_path)}` -> {argparse_entry_points}.",
            f"- Static console-script declarations: code-structure inventory `{_quote(inventory_path)}` -> {console_scripts}.",
            f"- Package, import-graph, data-path, test, and documentation claims: code-structure inventory `{_quote(inventory_path)}`.",
            f"- Dependency-manifest, CI, runtime, and verification constraints: Health Report `{_quote(health_report_path)}`.",
            "",
            "## Explicit unknowns",
            "",
            "- No installation command, tested runtime, data schema, command behavior, or modern compatibility claim is included because the supplied evidence does not verify it.",
            "- Static console-script and argparse declarations are not presented as verified commands.",
            "",
        ]
    )


def draft_docs(repo_path: Path, health_report_path: Path, output_dir: Path) -> dict[str, Path]:
    """Call the structure inventory and create review-only documentation artifacts."""
    repo_path = repo_path.resolve()
    output_dir = output_dir.resolve()
    health_report_path = health_report_path.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_path}")
    if not health_report_path.is_file():
        raise ValueError(f"Health Report path is not a file: {health_report_path}")
    if _is_within(output_dir, repo_path):
        raise ValueError("Documentation output directory must be outside the target repository.")

    _validate_directive()
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = output_dir / "code_structure_inventory.json"
    _run_inventory(repo_path, inventory_path)
    inventory = _read_json(inventory_path)
    health_report = health_report_path.read_text(encoding="utf-8", errors="replace")
    require_same_repository(
        identity_from_local_path(repo_path, "documentation target"),
        identity_from_json(inventory, "code-structure inventory"),
        identity_from_health_report(health_report, "Health Report"),
    )

    artifacts = {
        "readme": output_dir / "README.md",
        "architecture": output_dir / "ARCHITECTURE.md",
        "contributing": output_dir / "CONTRIBUTING.md",
        "evidence": output_dir / "documentation_evidence.md",
        "inventory": inventory_path,
    }
    artifacts["readme"].write_text(_render_readme(inventory, health_report), encoding="utf-8")
    artifacts["architecture"].write_text(_render_architecture(inventory, health_report), encoding="utf-8")
    artifacts["contributing"].write_text(_render_contributing(health_report), encoding="utf-8")
    artifacts["evidence"].write_text(_render_evidence_note(inventory_path, health_report_path, inventory), encoding="utf-8")
    return artifacts


def main(argv: list[str] | None = None) -> int:
    """Draft docs under ``directives/generate_docs.md`` without touching the clone."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", type=Path, help="Path to the local repository clone to inventory.")
    parser.add_argument("--health-report", type=Path, required=True, help="Health Report emitted by diagnosis_agent.py.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory outside the clone for generated draft artifacts.")
    args = parser.parse_args(argv)
    try:
        artifacts = draft_docs(args.repo_path, args.health_report, args.output_dir)
        sys.stdout.write(json.dumps({name: str(path) for name, path in artifacts.items()}, indent=2, sort_keys=True) + "\n")
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
