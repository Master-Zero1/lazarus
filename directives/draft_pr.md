# Draft Documentation PR SOP

## Goal

Open exactly one draft pull request on the operator-owned fork containing only
the reviewed regenerated documentation. Never modify application source code,
dependencies, tests, CI, licenses, or security-sensitive files. Never merge a
pull request and never target an upstream repository the operator does not own.

## Inputs

- Confirmed `fork_owner`, `fork_repo`, `expected_upstream` (the fork's parent
  as `owner/name`), and default target branch.
- `health_report`: the target repository's completed diagnosis output. Its
  observed constraint findings supply the PR body's legacy-constraints section.
- `docs_draft_dir`: reviewed documentation artifacts created by the Generate
  Documentation SOP.
- `docs_evidence_path`: claim-to-source mapping for PR description review.
- `operator_approval`: explicit authorization to create the draft PR.
- Optional `branch_name` and `pr_title`.

## Execution scripts

Run `execution/open_draft_pr.py` once after validating the inputs and requested
file allowlist. The script must receive the operator-owned fork identity, never
an upstream identity inferred from repository metadata.

## Outputs

- A draft PR URL/number and branch name.
- A machine-readable creation receipt containing the fork identity, base branch,
  exact documentation-file allowlist, created commit SHA, and draft status.
- A PR body that explains scope, only the target Health Report's actual legacy
  constraints, and that no application code was changed.

## Edge cases

- Missing explicit operator approval, uncertain fork ownership, a non-draft
  response, or a target that resolves to upstream must halt the operation.
- A missing/mismatched Health Report or a fork parent that differs from the
  supplied `expected_upstream` must halt the operation; do not reuse findings
  or an upstream identity from another repository.
- Existing open documentation PRs, name collisions, dirty generated artifacts,
  and no-op documentation diffs require a report and human decision; do not
  silently append unrelated changes.
- If the candidate set contains any non-documentation file, reject it. Never
  broaden the allowlist to include source, dependency, test, CI, license, or
  configuration files.
- On partial remote failure, report the branch/PR state exactly as observed; do
  not retry in a way that could create duplicate PRs.
