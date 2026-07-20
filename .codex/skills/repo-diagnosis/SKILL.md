---
name: repo-diagnosis
description: Diagnose a stale repository's declared runtime, dependency, and CI health from deterministic Layer 3 findings. Use when Lazarus needs to inventory dependency manifests, apply a documentation fallback when manifests are absent, distinguish exact pins from version bounds, or report CI presence/absence without executing repository code.
---

# Repository Diagnosis

## Scope

Produce a traceable Health Report from Layer 3 evidence. Do not execute the
repository, install or upgrade dependencies, modify the clone, or infer a
supported modern environment from an old declaration.

Call the deterministic execution scripts in this order:

1. `execution/inventory_manifests.py`
2. `execution/check_dependency_freshness.py`
3. `execution/parse_ci_config.py`

Keep observed facts, reasonable inferences, and unknowns separate in the
Health Report. Record each dependency claim with its source file and line.

## Dependency evidence hierarchy

1. Prefer a conventional manifest when present: `requirements*.txt`,
   `pyproject.toml`, `setup.py`, or another supported packaging manifest.
2. If none is present, use an explicit README or project-documentation
   declaration as a **documentation-derived fallback**. Preserve the exact
   text, source path, and line number; do not represent it as an installable
   or complete manifest.
3. If neither manifests nor explicit documentation declarations exist, report
   the dependency set as unknown. Do not invent a requirements file or setup
   command.

For example, the Facial-Expression-Recognition.Pytorch fixture has no
conventional manifest. Its `Readme.md` explicitly declares `Python ==2.7` and
`Pytorch >=0.2.0`; these are valid diagnosis evidence, but not proof that the
full environment is reproducible.

## Version semantics

- Treat `==` (or an equivalent exact-version expression) as an **exact pin**.
  Report the declared version and assess its lifecycle without changing it.
- Treat `>=`, `>`, `<=`, `<`, compatible-release ranges, and unconstrained
  names as **bounds or declarations, not pins**. In particular, `Pytorch
  >=0.2.0` is a legacy lower bound: it does not establish compatibility with a
  current PyTorch release.
- Preserve unversioned declarations such as `h5py` or `sklearn` as
  unversioned. Report the missing bound as a reproducibility gap.
- Never label a dependency obsolete solely because it has a lower bound. State
  what the evidence supports, what is unknown, and which human decision or
  validation is required next.

## CI findings

Use `parse_ci_config.py` to inspect supported repository-local CI locations,
including GitHub Actions and conventional CI configuration files.

When none is found, emit a structured finding such as:

```json
{
  "ci_configuration_found": false,
  "finding": "no_ci_configuration_found"
}
```

Treat this as a diagnosis finding, not an execution error. Explain that there
is no repository-local automated-verification configuration in the inspected
locations; retain the limit that CI may exist elsewhere or outside the parser's
supported locations.

When a CI configuration is found, do not treat it as proof that CI currently
works. Emit a separate provider-lifecycle finding when static evidence points
to a retired or legacy service (for example, an unverified Travis configuration
after the `travis-ci.org` shutdown), and state the active endpoint and recent
build status as unknown until a maintainer verifies them.

## Health Report minimums

- List manifest discovery results and any documentation fallback separately.
- State runtime lifecycle risks, incomplete dependency specification, and CI
  absence as revival blockers when the evidence supports them.
- Mark runtime execution, complete dependency compatibility, test coverage,
  and external CI as unknown unless a supplied deterministic finding confirms
  them.
- Preserve a successful report when a manifest or CI is absent. Only malformed
  inputs or a failed execution script should halt diagnosis.
