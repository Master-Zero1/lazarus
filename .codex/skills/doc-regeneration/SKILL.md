---
name: doc-regeneration
description: Regenerate accurate repository documentation from static code structure without executing untrusted code. Use when Lazarus needs AST-based inventory of argparse options, module docstrings and signatures, local import graphs, or source-backed handling of documentation inconsistencies.
---

# Static Documentation Regeneration

## Scope

Draft documentation from the repository's source tree and supplied Health
Report, not from stale README prose or executed code. Keep generated artifacts
outside the target clone. Do not install dependencies, import repository
modules, run entry points, or claim that a workflow was validated.

Call `execution/inventory_code_structure.py` to produce the deterministic
inventory, then use its evidence to draft the README, architecture notes, and
contributing guide.

## AST-only code inventory

Parse Python source with `ast.parse`; never import or execute it. For each
parseable source file, record a parse status and preserve a syntax/read error
as an inventory finding rather than falling back to execution.

- Find `argparse.ArgumentParser` markers and literal `.add_argument(...)`
  calls. Extract literal flag names, `type`, `default`, `action`, and `help`
  values when they can be read statically. Do not evaluate variables, imports,
  defaults, or arbitrary expressions to fill gaps.
- Extract literal `setup.py` `entry_points["console_scripts"]` declarations
  as static command-to-target metadata. Do not import `setup.py`, invoke the
  declared command, or represent the declaration as an installed executable.
- Use `ast.get_docstring` for package/module/class/function docstrings and
  record top-level function/class signatures from AST nodes. Mark dynamic or
  unparseable values as unknown.
- Build the local import graph from `Import` and `ImportFrom` nodes, resolving
  only modules known to be local to the repository. Do not infer runtime
  imports, optional dependencies, or call order.

## Static CLI candidate language

Describe a file with an argparse marker as a **static CLI candidate**. This
means the source contains statically detected parser structure; it does not
prove that the script runs, accepts every documented flag in the current
environment, has complete setup instructions, or is a supported command.

Use language such as: “static AST extraction found these source-text options”
and “not a verified command reference.” Preserve this distinction in README
and evidence notes, especially for legacy repositories with unverified Python
or dependency compatibility.

## Source inconsistencies

Preserve source-derived values even when they appear wrong. Add a concise,
source-attributed note when a literal value is likely an upstream authoring
mistake; do not silently correct or hide it, and do not call it a Lazarus
extraction failure.

For example, `mainpro_FER.py` declares `--dataset` with help text “CNN
architecture” and `--bs` with help text “learning rate.” Document the exact
AST-extracted values and note that the help text appears copy-pasted in the
original source. Attribute the observation to the source text, not to runtime
behavior or a confirmed upstream defect.

## Documentation boundaries

- Tie material claims to inventory paths and Health Report findings.
- State absent tests, CI, manifests, setup commands, dataset completeness,
  current compatibility, and runtime behavior as unknown unless the supplied
  evidence establishes them.
- Do not modernize legacy requirements by implication. Keep Python 2.7 and
  lower-bound-only PyTorch declarations as documented constraints, not current
support claims.

Describe a literal setuptools console-script entry as a **static console-script
declaration**. It is different evidence from argparse, but it has the same
boundary: it identifies source metadata, not a verified command or a successful
installation.
- Keep an evidence note that maps generated claims to inventory sections and
  lists explicit unknowns for reviewer follow-up.
