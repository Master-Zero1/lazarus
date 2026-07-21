"""Inventory dependency manifests and documentation-declared runtime requirements.

Implements the inventory step in ``directives/diagnose_repo.md``.  The script
only reads the supplied repository and writes JSON when ``--output`` is used;
it never executes repository code or changes dependency files.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


MANIFEST_NAMES = {
    "pyproject.toml": "pyproject",
    "setup.py": "setup_py",
    "pipfile": "pipfile",
    "pipfile.lock": "pipfile_lock",
    "environment.yml": "conda_environment",
    "environment.yaml": "conda_environment",
    "conda.yml": "conda_environment",
    "conda.yaml": "conda_environment",
    "package.json": "package_json",
}
README_NAMES = {"readme.md", "readme.rst", "readme.txt"}
REQUIREMENTS_RE = re.compile(r"^requirements(?:[-_.].*)?\.txt$", re.IGNORECASE)
PACKAGE_JSON_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
MARKDOWN_DEPENDENCY_RE = re.compile(
    r"^\s*[-*]\s*(?P<name>[A-Za-z][A-Za-z0-9_.-]*)"
    r"(?:\s*(?:(?P<operator>===|==|>=|<=|~=|!=|>|<|=)\s*)?"
    r"(?P<version>v?\d+(?:\.\d+){0,3}(?:[A-Za-z0-9.+_-]+)?))?"
    r"(?:\s*\([^)]*\))?\s*$",
    re.IGNORECASE,
)
REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.-]+)\s*"
    r"(?P<specifier>"
    r"(?:(?:===|==|>=|<=|~=|!=|>|<)\s*[^;\s,]+"
    r"(?:\s*,\s*(?:===|==|>=|<=|~=|!=|>|<)\s*[^;\s,]+)*)"
    r")?"
)


def _relative(repo_path: Path, path: Path) -> str:
    return path.relative_to(repo_path).as_posix()


def _dependency(
    name: str,
    specifier: str | None,
    source_path: str,
    line: int,
    raw: str,
    source_kind: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "normalized_name": name.lower().replace("_", "-").replace(".", "-"),
        "specifier": specifier,
        "source": {"path": source_path, "line": line, "kind": source_kind},
        "raw": raw,
    }


def _parse_requirements(path: Path, repo_path: Path) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        candidate = raw_line.split("#", 1)[0].strip()
        if not candidate or candidate.startswith(("-r", "--requirement", "-e", "--editable")):
            continue
        match = REQUIREMENT_RE.match(candidate)
        if match:
            dependencies.append(
                _dependency(
                    match.group("name"),
                    match.group("specifier").replace(" ", "") if match.group("specifier") else None,
                    _relative(repo_path, path),
                    line_number,
                    raw_line,
                    "requirements_file",
                )
            )
    return dependencies


def _literal_string_list(node: ast.AST) -> list[tuple[str, int]] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values: list[tuple[str, int]] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append((element.value, getattr(element, "lineno", getattr(node, "lineno", 1))))
    return values


def _parse_setup_py(path: Path, repo_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as error:
        return [], f"Could not statically parse setup.py: {error.msg} at line {error.lineno}."

    dependencies: list[dict[str, Any]] = []
    warnings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg not in {"install_requires", "requires"}:
                continue
            values = _literal_string_list(keyword.value)
            if values is None:
                warnings.append(
                    f"Could not statically parse {keyword.arg} in setup.py at line {getattr(keyword.value, 'lineno', node.lineno)}; "
                    "non-literal declared dependencies were not included."
                )
                continue
            for value, line_number in values:
                match = REQUIREMENT_RE.match(value)
                if match:
                    dependencies.append(
                        _dependency(
                            match.group("name"),
                            match.group("specifier").replace(" ", "") if match.group("specifier") else None,
                            _relative(repo_path, path),
                            line_number,
                            value,
                            "setup_py_static",
                        )
                    )
    return dependencies, " ".join(warnings) if warnings else None


def _json_property_line(path: Path, property_name: str) -> int:
    """Return a best-effort source line for a JSON property without executing it."""
    property_re = re.compile(r'"' + re.escape(property_name) + r'"\s*:')
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if property_re.search(raw_line):
            return line_number
    return 1


def _parse_package_json(path: Path, repo_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """Read literal npm dependency sections from a root package.json file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as error:
        return [], {}, "Could not parse package.json: {0} at line {1}.".format(error.msg, error.lineno)
    if not isinstance(payload, dict):
        return [], {}, "package.json must contain a JSON object at its root."

    dependencies: list[dict[str, Any]] = []
    parsed_sections: list[str] = []
    for section in PACKAGE_JSON_DEPENDENCY_SECTIONS:
        entries = payload.get(section)
        if entries is None:
            continue
        if not isinstance(entries, dict):
            return [], {}, "package.json field {0!r} is not an object.".format(section)
        parsed_sections.append(section)
        for name, specifier in sorted(entries.items(), key=lambda item: str(item[0]).lower()):
            if not isinstance(name, str) or not isinstance(specifier, str):
                continue
            dependencies.append(
                _dependency(
                    name,
                    specifier or None,
                    _relative(repo_path, path),
                    _json_property_line(path, name),
                    '"{0}": "{1}"'.format(name, specifier),
                    "package_json_{0}".format(section),
                )
            )
    metadata = {key: payload[key] for key in ("name", "version", "private") if key in payload}
    metadata["dependency_sections"] = parsed_sections
    return dependencies, metadata, None


def _parse_documented_dependencies(path: Path, repo_path: Path) -> list[dict[str, Any]]:
    """Parse simple README dependency bullets without treating prose as commands."""
    dependencies: list[dict[str, Any]] = []
    in_dependencies_section = False
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        heading = raw_line.strip().lower().strip("#").strip()
        if heading:
            if "dependenc" in heading or "requirement" in heading:
                in_dependencies_section = True
                continue
            if raw_line.lstrip().startswith("#"):
                in_dependencies_section = False
        if not in_dependencies_section:
            continue
        match = MARKDOWN_DEPENDENCY_RE.match(raw_line)
        if not match:
            continue
        operator = match.group("operator")
        version = match.group("version")
        specifier = None
        if version:
            # A bare documented version such as ``Python 2.7`` is a stated
            # runtime target, so normalize it as an exact constraint while
            # retaining the original line as evidence.
            specifier = f"{operator or '=='}{version}"
        dependencies.append(
            _dependency(
                match.group("name"),
                specifier,
                _relative(repo_path, path),
                line_number,
                raw_line,
                "readme_dependency_section",
            )
        )
    return dependencies


def _candidate_files(repo_path: Path) -> Iterable[Path]:
    for path in sorted(repo_path.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file():
            yield path


def inventory_manifests(repo_path: Path) -> dict[str, Any]:
    """Return a deterministic inventory of conventional and documented requirements."""
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_path}")

    manifests: list[dict[str, Any]] = []
    documented_dependencies: list[dict[str, Any]] = []
    warnings: list[str] = []

    for path in _candidate_files(repo_path):
        filename = path.name.lower()
        manifest_kind = MANIFEST_NAMES.get(filename)
        if REQUIREMENTS_RE.match(path.name):
            dependencies = _parse_requirements(path, repo_path)
            manifests.append({"path": _relative(repo_path, path), "kind": "requirements", "dependencies": dependencies, "parse_status": "parsed"})
        elif manifest_kind == "setup_py":
            dependencies, warning = _parse_setup_py(path, repo_path)
            manifests.append({"path": _relative(repo_path, path), "kind": manifest_kind, "dependencies": dependencies, "parse_status": "parsed" if warning is None else "incomplete"})
            if warning:
                warnings.append(warning)
        elif manifest_kind == "package_json":
            dependencies, metadata, warning = _parse_package_json(path, repo_path)
            manifests.append(
                {
                    "path": _relative(repo_path, path),
                    "kind": manifest_kind,
                    "dependencies": dependencies,
                    "metadata": metadata,
                    "parse_status": "parsed" if warning is None else "incomplete",
                }
            )
            if warning:
                warnings.append(warning)
        elif manifest_kind:
            manifests.append({"path": _relative(repo_path, path), "kind": manifest_kind, "dependencies": [], "parse_status": "unsupported"})
            warnings.append(f"Found {path.name}, but parsing for {manifest_kind} is not implemented yet.")
        elif filename in README_NAMES:
            documented_dependencies.extend(_parse_documented_dependencies(path, repo_path))

    if not manifests:
        warnings.append("No conventional dependency manifest was found at the repository root.")
    if documented_dependencies and not manifests:
        warnings.append("Runtime requirements were found only in repository documentation, not a conventional manifest.")

    return {
        "schema_version": 1,
        "repository": str(repo_path),
        "conventional_manifests": manifests,
        "documented_runtime_requirements": documented_dependencies,
        "warnings": warnings,
    }


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_output_path(repo_path: Path, output_path: Path | None) -> None:
    """Keep an optional inventory receipt outside the repository being read."""
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
    """Run the manifest inventory named by ``directives/diagnose_repo.md``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", type=Path, help="Path to the local repository clone to inspect.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path; stdout is used by default.")
    args = parser.parse_args(argv)
    try:
        _validate_output_path(args.repo_path, args.output)
        _write_json(inventory_manifests(args.repo_path), args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
