"""Create a deterministic, read-only source-structure inventory.

Implements the execution step in ``directives/generate_docs.md``. It inventories
paths and static textual entry-point markers only; it never imports, executes,
or changes code in the inspected repository.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Any, Iterator


IGNORED_DIRECTORIES = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__"}
CONFIGURATION_SUFFIXES = {".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"}
DOCUMENTATION_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
DATA_DIRECTORY_NAMES = {"data", "dataset", "datasets", "training_data", "test_data", "ck+48"}
ASSET_DIRECTORY_NAMES = {"assets", "images", "demo", "examples", "static"}
ARGPARSE_RE = re.compile(r"\bargparse\.ArgumentParser\s*\(")
MAIN_GUARD_RE = re.compile(r"if\s+__name__\s*==\s*['\"]__main__['\"]")
CONSOLE_SCRIPT_RE = re.compile(
    r"^\s*(?P<command>[A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*"
    r"(?P<target>[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_.]*)\s*$"
)


def _relative(repo_path: Path, path: Path) -> str:
    return path.relative_to(repo_path).as_posix()


def _walk_files(repo_path: Path) -> Iterator[Path]:
    """Yield repository files in stable order while excluding VCS and cache trees."""
    for root, directory_names, file_names in os.walk(repo_path):
        directory_names[:] = sorted(
            (name for name in directory_names if name not in IGNORED_DIRECTORIES),
            key=str.lower,
        )
        for filename in sorted(file_names, key=str.lower):
            yield Path(root) / filename


def _module_name(repo_path: Path, path: Path) -> str:
    relative = path.relative_to(repo_path)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__init__"


def _entry_point_candidate(repo_path: Path, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    signals: list[str] = []
    if ARGPARSE_RE.search(text):
        signals.append("argparse.ArgumentParser")
    if MAIN_GUARD_RE.search(text):
        signals.append("__main__ guard")
    console_script_analysis = _analyze_console_scripts(path) if path.name == "setup.py" else None
    if console_script_analysis and console_script_analysis["console_scripts"]:
        signals.append("setup.py entry_points.console_scripts")
    item: dict[str, Any] = {
        "path": _relative(repo_path, path),
        "kind": (
            "static_cli_candidate"
            if "argparse.ArgumentParser" in signals
            else "static_console_script_entry_point"
            if "setup.py entry_points.console_scripts" in signals
            else "root_python_script_candidate"
        ),
        "signals": signals,
    }
    if "argparse.ArgumentParser" in signals:
        item["static_analysis"] = _analyze_argparse(path)
    if console_script_analysis is not None:
        item["console_script_analysis"] = console_script_analysis
    return item


def _expression_label(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Constant):
            return repr(node.value)
        return "<static expression unavailable>"


def _literal_or_expression(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return {"expression": _expression_label(node)}


def _parse_tree(path: Path) -> tuple[ast.Module | None, str | None]:
    try:
        # Static parsing can emit SyntaxWarning for legacy literal escapes.
        # Keep analysis deterministic and report only parse failures; never
        # execute a module merely to inspect it.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path)), None
    except SyntaxError as error:
        return None, f"SyntaxError at line {error.lineno}: {error.msg}"


def _literal_string_entries(node: ast.AST) -> list[tuple[str, int]] | None:
    """Return literal strings and source lines from a list-like AST node only."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    entries: list[tuple[str, int]] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        entries.append((element.value, getattr(element, "lineno", getattr(node, "lineno", 1))))
    return entries


def _analyze_console_scripts(path: Path) -> dict[str, Any]:
    """Extract literal setuptools ``console_scripts`` declarations via AST only."""
    tree, parse_error = _parse_tree(path)
    if tree is None:
        return {"parse_status": "syntax_error", "error": parse_error, "console_scripts": [], "unparsed_entries": []}

    console_scripts: list[dict[str, Any]] = []
    unparsed_entries: list[dict[str, Any]] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _call_name(call) not in {"setup", "setuptools.setup"}:
            continue
        entry_points = next((keyword.value for keyword in call.keywords if keyword.arg == "entry_points"), None)
        if not isinstance(entry_points, ast.Dict):
            continue
        for key, value in zip(entry_points.keys, entry_points.values):
            if not isinstance(key, ast.Constant) or key.value != "console_scripts":
                continue
            entries = _literal_string_entries(value)
            if entries is None:
                unparsed_entries.append({"line": getattr(value, "lineno", call.lineno), "reason": "console_scripts is not a literal list of strings"})
                continue
            for raw, line in entries:
                match = CONSOLE_SCRIPT_RE.match(raw)
                if match:
                    console_scripts.append({"command": match.group("command"), "target": match.group("target"), "line": line, "raw": raw})
                else:
                    unparsed_entries.append({"line": line, "raw": raw, "reason": "unsupported console-script declaration format"})
    console_scripts.sort(key=lambda item: (item["command"], item["line"]))
    return {"parse_status": "parsed", "console_scripts": console_scripts, "unparsed_entries": unparsed_entries}


def _call_name(call: ast.Call) -> str | None:
    return _expression_label(call.func)


def _analyze_argparse(path: Path) -> dict[str, Any]:
    """Extract literal argparse definitions from AST nodes without evaluating them."""
    tree, parse_error = _parse_tree(path)
    if tree is None:
        return {"parse_status": "syntax_error", "error": parse_error, "parsers": [], "arguments": []}

    parser_names: set[str] = set()
    parsers: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or _call_name(value) != "argparse.ArgumentParser":
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                parser_names.add(target.id)
                description = next((keyword.value for keyword in value.keywords if keyword.arg == "description"), None)
                parsers.append({"variable": target.id, "line": node.lineno, "description": _literal_or_expression(description)})

    arguments: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id not in parser_names:
            continue
        flags = [_literal_or_expression(argument) for argument in node.args]
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
        arguments.append(
            {
                "parser": node.func.value.id,
                "line": node.lineno,
                "flags": flags,
                "type": _expression_label(keywords.get("type")),
                "default": _literal_or_expression(keywords.get("default")),
                "help": _literal_or_expression(keywords.get("help")),
                "action": _literal_or_expression(keywords.get("action")),
            }
        )
    arguments.sort(key=lambda item: item["line"])
    parsers.sort(key=lambda item: item["line"])
    return {"parse_status": "parsed", "parsers": parsers, "arguments": arguments}


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f"{node.name}({_expression_label(node.args) or ''})"


def _class_signature(node: ast.ClassDef) -> str:
    bases = [_expression_label(base) or "<unknown>" for base in node.bases]
    keywords = [f"{keyword.arg}={_expression_label(keyword.value)}" for keyword in node.keywords if keyword.arg]
    arguments = ", ".join([*bases, *keywords])
    return f"{node.name}({arguments})" if arguments else node.name


def _analyze_package_module(repo_path: Path, path: Path) -> dict[str, Any]:
    """Extract module docstrings and top-level definitions through AST only."""
    tree, parse_error = _parse_tree(path)
    result: dict[str, Any] = {
        "path": _relative(repo_path, path),
        "module": _module_name(repo_path, path),
        "parse_status": "parsed" if tree else "syntax_error",
        "docstring": None,
        "top_level_definitions": [],
    }
    if tree is None:
        result["error"] = parse_error
        return result
    result["docstring"] = ast.get_docstring(tree)
    definitions: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.append({"kind": "function", "name": node.name, "signature": _signature(node), "line": node.lineno, "docstring": ast.get_docstring(node)})
        elif isinstance(node, ast.ClassDef):
            definitions.append({"kind": "class", "name": node.name, "signature": _class_signature(node), "line": node.lineno, "docstring": ast.get_docstring(node)})
    result["top_level_definitions"] = definitions
    return result


def _local_import_edges(repo_path: Path, python_files: list[Path]) -> dict[str, Any]:
    """Build import edges only when the target resolves to an inventoried local module."""
    module_by_path = {_relative(repo_path, path): _module_name(repo_path, path) for path in python_files}
    local_modules = set(module_by_path.values())
    edges: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    for path in python_files:
        source = module_by_path[_relative(repo_path, path)]
        tree, parse_error = _parse_tree(path)
        if tree is None:
            parse_errors.append({"path": _relative(repo_path, path), "error": parse_error or "Syntax error"})
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            statement = ""
            if isinstance(node, ast.Import):
                statement = "import " + ", ".join(alias.name for alias in node.names)
                for alias in node.names:
                    if alias.name in local_modules:
                        targets.append(alias.name)
                    elif alias.name.split(".")[0] in local_modules:
                        targets.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                statement = f"from {node.module} import " + ", ".join(alias.name for alias in node.names)
                for alias in node.names:
                    candidate = f"{node.module}.{alias.name}" if alias.name != "*" else node.module
                    if candidate in local_modules:
                        targets.append(candidate)
                    elif node.module in local_modules:
                        targets.append(node.module)
            for target in sorted(set(targets)):
                edges.append({"from": source, "to": target, "line": node.lineno, "statement": statement})
    edges.sort(key=lambda item: (item["from"], item["to"], item["line"]))
    return {"nodes": sorted(local_modules), "edges": edges, "parse_errors": parse_errors}


def _data_path(repo_path: Path, path: Path, classification: str) -> dict[str, Any]:
    file_count = sum(1 for candidate in _walk_files(path))
    return {"path": _relative(repo_path, path), "classification": classification, "file_count": file_count}


def inventory_code_structure(repo_path: Path) -> dict[str, Any]:
    """Inventory code, documentation, data, configuration, and test paths."""
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_path}")

    all_files = list(_walk_files(repo_path))
    python_files = [path for path in all_files if path.suffix.lower() == ".py"]
    package_directories = sorted(
        {path.parent for path in python_files if path.name == "__init__.py"},
        key=lambda path: _relative(repo_path, path).lower(),
    )
    package_paths = {path.resolve() for path in package_directories}

    packages = [
        {
            "path": _relative(repo_path, directory),
            "module": _module_name(repo_path, directory / "__init__.py"),
            "modules": [
                _relative(repo_path, path)
                for path in sorted(directory.glob("*.py"), key=lambda item: item.name.lower())
            ],
        }
        for directory in package_directories
    ]
    modules = [
        {
            "path": _relative(repo_path, path),
            "module": _module_name(repo_path, path),
            "location": "package" if path.parent.resolve() in package_paths else "root",
        }
        for path in python_files
        if path.name != "__init__.py"
    ]
    modules.sort(key=lambda item: item["path"].lower())
    package_module_analysis = [
        _analyze_package_module(repo_path, path)
        for path in python_files
        if path.parent.resolve() in package_paths
    ]
    package_module_analysis.sort(key=lambda item: item["path"].lower())

    entry_points = [
        _entry_point_candidate(repo_path, path)
        for path in python_files
        if path.parent == repo_path
    ]
    entry_points.sort(key=lambda item: item["path"].lower())

    configuration_files = [
        {"path": _relative(repo_path, path), "format": path.suffix.lower().lstrip(".")}
        for path in all_files
        if path.suffix.lower() in CONFIGURATION_SUFFIXES
    ]
    configuration_files.sort(key=lambda item: item["path"].lower())

    root_directories = sorted((path for path in repo_path.iterdir() if path.is_dir() and path.name not in IGNORED_DIRECTORIES), key=lambda item: item.name.lower())
    data_paths: list[dict[str, Any]] = []
    for directory in root_directories:
        name = directory.name.lower()
        if name in DATA_DIRECTORY_NAMES:
            data_paths.append(_data_path(repo_path, directory, "dataset_or_preprocessed_data"))
        elif name in ASSET_DIRECTORY_NAMES:
            data_paths.append(_data_path(repo_path, directory, "demo_or_visual_asset"))

    test_files = [
        path for path in python_files
        if any(part.lower() in {"test", "tests"} for part in path.relative_to(repo_path).parts[:-1])
        or path.name.lower().startswith("test_")
        or path.name.lower().endswith("_test.py")
    ]
    test_directories = [
        _relative(repo_path, directory)
        for directory in root_directories
        if directory.name.lower() in {"test", "tests"}
    ]
    documentation_files = [
        {
            "path": _relative(repo_path, path),
            "kind": "license" if path.name.lower().startswith("license") else "documentation",
        }
        for path in all_files
        if path.suffix.lower() in DOCUMENTATION_SUFFIXES or path.name.lower().startswith("license")
    ]
    documentation_files.sort(key=lambda item: item["path"].lower())

    findings: list[dict[str, str]] = []
    if not configuration_files:
        findings.append({"code": "no_configuration_files_found", "message": "No supported repository configuration files were found."})
    if not test_files:
        findings.append({"code": "no_tests_detected", "message": "No Python test files or root test directories were detected by naming convention."})

    return {
        "schema_version": 1,
        "repository": str(repo_path),
        "entry_points": entry_points,
        "packages": packages,
        "modules": modules,
        "package_module_analysis": package_module_analysis,
        "import_graph": _local_import_edges(repo_path, python_files),
        "configuration_files": configuration_files,
        "data_paths": data_paths,
        "tests": {
            "test_directories": test_directories,
            "test_files": [_relative(repo_path, path) for path in sorted(test_files, key=lambda item: _relative(repo_path, item).lower())],
        },
        "documentation_files": documentation_files,
        "findings": findings,
    }


def _write_json(payload: dict[str, Any], output_path: Path | None) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output_path is None:
        sys.stdout.write(rendered)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the structure inventory named by ``directives/generate_docs.md``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_path", type=Path, help="Path to the local repository clone to inspect.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path; stdout is used by default.")
    args = parser.parse_args(argv)
    try:
        _write_json(inventory_code_structure(args.repo_path), args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
