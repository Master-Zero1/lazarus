<div align="center">

# Lazarus

### Evidence-backed diagnosis and documentation regeneration for stale, abandoned-but-valuable open-source repositories.

Lazarus tells a maintainer what is actually in a codebase, what is actually wrong, and what a responsible revival would require. It does not repair or modify the target repository.

</div>

---

## The problem

Open-source software does not always die because it stops working. Often, it dies because nobody can tell, at a glance, whether it still works, and figuring that out takes more effort than most people are willing to spend before they have even decided the project is worth their time.

Every maintainer who has inherited an abandoned repository knows the pattern. The README is stale. The dependency file, if there is one, contains version bounds nobody has exercised in years. The issue tracker has forty open questions and a pile of old pull requests. One may be noise; another may be a real fix that simply never got merged. You do not know whether the project runs, what it expects from a contributor, or where to begin. So you close the tab.

**Lazarus exists to close the gap between “I do not know” and “I know exactly what needs human attention.”**

It is not a repair bot. It does not edit source code, install unknown dependencies, execute repository code, run the target test suite, close issues, or merge pull requests. It reads a repository the way a careful senior engineer would on their first day inheriting it, then returns evidence instead of a guess.

Point Lazarus at a repository and it can:

- **Diagnose** declared runtimes, dependencies, CI configuration, and statically discovered test paths. It distinguishes parsed manifests from README fallback evidence, exact dependency pins from lower bounds, and facts from unknowns.
- **Regenerate documentation from actual code structure** instead of copying a stale README. It statically inventories packages, entry points, imports, CLI definitions, configuration, data paths, tests, and documentation files.
- **Triage an issue and pull-request backlog** into duplicate/resolved, obsolete, still valid, and **valuable but stalled by inactivity**. An old conflicted pull request is not automatically noise.
- **Preview a documentation-only draft pull request** against an operator-controlled fork. A live draft PR is separately approval-gated; application code is never included.
- **Synthesize a Revival Report** with an executive summary, risks, unknowns, artifact traceability, and a phased human decision checklist.

The important property is not that Lazarus produces a polished report. It is that the report is built from identifiable evidence. Material findings retain source paths, API-snapshot references, or prior-stage artifact paths. Unknowns are named instead of silently omitted. Confidence is tied to the available evidence, not merely to the fact that a data object exists.

**Lazarus does not revive dead code. It explains what reviving it would take, so a maintainer can decide whether the work is worth doing and start prepared.**

## Architecture

Lazarus separates instructions, judgement, and deterministic operations.

```text
Layer 1: Directives
    Markdown SOPs in directives/ define goals, inputs, outputs,
    safety boundaries, and edge cases.

Layer 2: Orchestration
    Agents in src/lazarus/agents/ read the SOPs, validate artifact
    identity, route stages, and synthesize bounded conclusions.

Layer 3: Execution
    Deterministic scripts in src/lazarus/execution/ clone, inspect,
    parse, fetch, and prepare guarded PR actions.
```

The current pipeline uses one resolved local checkout:

```text
Clone
  -> Diagnose
  -> Generate documentation
  -> Triage issues and pull requests
  -> Draft-PR preview
  -> Synthesize Revival Report
```

The optional API and frontend are not additional decision-making layers. The
API launches the installed `lazarus` command in a separate process; the
frontend communicates only with that local API.

### Layer 2 agents

- `diagnosis_agent.py` runs the required deterministic diagnosis inventory
  scripts and synthesizes their output into a Health Report. It does not fix
  the target project or open a pull request.
- `docs_agent.py` calls the static structure inventory and writes draft
  documentation outside the target clone. It does not execute or modify target
  code.
- `triage_agent.py` reads supplied GitHub snapshots and makes non-destructive
  recommendations. It does not close, label, merge, or comment on issues or
  pull requests.
- `pr_agent.py` reads the draft-PR SOP and delegates to the guarded execution
  script. It does not duplicate fork verification, file allowlisting, or
  GitHub-write safety logic.
- `synthesis_agent.py` validates and combines existing artifacts into a
  Revival Report. It does not re-run inventory, fetch GitHub data, or
  re-investigate the repository.
- `orchestrator.py` runs the complete ordered pipeline from one clone and
  always invokes the PR stage in preview mode. It never forwards live-execute
  approval to the PR agent.

### Layer 3 execution scripts

The current deterministic scripts are:

```text
clone_repo.py                   Read-only Git clone with a receipt
inventory_manifests.py          Manifest and documented-runtime inventory
check_dependency_freshness.py   Offline constraint classification
parse_ci_config.py              CI configuration inventory
inventory_code_structure.py     Static AST and source-tree inventory
fetch_issues.py                 Read-only GitHub issue metadata fetch
fetch_prs.py                    Read-only GitHub pull-request metadata fetch
open_draft_pr.py                Preview or explicitly approved draft-PR action
```

## How this was built with Codex

Lazarus was built by Master-Zero1 with Codex and GPT-5.6 as an engineering
collaborator. The project was not generated in one pass.

The process started with the directives, then Layer 3 script stubs, then real
logic verified stage by stage against
`WuJie1010/Facial-Expression-Recognition.Pytorch`. Documentation inventory,
dependency diagnosis, CI detection, issue triage, draft-PR preview, and
synthesis were each checked before the next stage was added.

The same pipeline was then exercised against `veekun/pokedex`, a structurally
different repository with conventional manifests, package entry points, a much
larger issue backlog, GitHub Actions, and a Travis configuration. That exposed
the difference between a repository that has no conventional manifest and one
whose declarations can be parsed directly.

A dedicated audit pass found real cross-cutting problems. They were fixed and
regression-checked against the fixture repositories:

- **Artifact identity could be blended across repositories.** A diagnosis from
  one local checkout could previously be paired with another repository’s
  remote data. Artifact identity is now validated at every stage boundary.
  A supplied Pokedex documentation inventory paired with the
  Facial-Expression expected upstream is rejected with a message beginning
  `Repository identity mismatch`, rather than producing a believable but
  invalid report.
- **Duplicate detection was too trusting of shared tracker boilerplate.**
  Migrated Pokedex issues shared structural Redmine headers, which inflated
  text-overlap scores. The triage logic now removes that migration wrapper
  before comparing the meaningful issue content.
- **A generic resolution signal was too broad.** The phrase “duplicate of”
  could describe a domain concept, such as a duplicate game-data record,
  rather than an issue tracker relationship. Resolution matching was narrowed
  so it requires a tracker-specific signal.
- **A receipt status alone was not enough to prove a live PR.** Synthesis now
  requires the complete live-receipt shape: live mode, a created commit SHA,
  a canonical GitHub pull-request URL, and `target.draft: true`. A status
  string by itself cannot be presented as proof of a draft PR.
- **Malformed snapshots could look empty.** Null collections, wrong JSON
  top-level types, and all-non-object items are now rejected or disclosed as
  exclusions rather than producing a clean-looking report with no
  classifications.

After the pipeline work, Lazarus was packaged as `lazarus-revival`, then
wrapped in an optional local FastAPI transport, then given a React dashboard
and Evidence PDF export. The core pipeline remains standard-library-only.

## What is actually verified

The rows below come from saved receipts and generated reports in this
repository. GitHub state can change after a snapshot or receipt is created, so
the linked PR facts describe the recorded action rather than a claim about
current remote state.

| Case | Verified result |
| --- | --- |
| Facial-Expression full pipeline | A saved full `run_receipt.json` records `status: completed` for clone, diagnose, documentation generation, triage, and synthesis; the PR stage is correctly recorded as `preview_generated`. The resolved checkout was `fefe6653f3e09912693a73c54e6a8247adca3090`. |
| Pokedex full pipeline | A saved full `run_receipt.json` records the same completed five stages and one draft-PR preview for `veekun/pokedex`, resolved at `cc483e1877f22b8c19ac27ec0ff5fafd09c5cd5b`. |
| Facial-Expression diagnosis | The Health Report records no conventional dependency manifest, a README-declared `Python ==2.7`, a lower-bound `Pytorch >=0.2.0`, and no supported repository-local CI configuration. |
| Facial-Expression triage | The saved report contains 1 `duplicate/resolved`, 0 `obsolete`, 40 `still valid`, and 3 `valuable-but-stalled-by-inactivity` classifications. It retains PR #70 and conflicted PR #95 for human review rather than dismissing them because of age or conflict state. |
| Pokedex diagnosis | The Health Report records 2 conventional dependency manifests, including exact `construct ==2.5.3`, several bounded or lower-bound dependency declarations, GitHub Actions configuration, and a Travis configuration whose operational status remains unverified. |
| Pokedex triage | The saved report contains 1 `duplicate/resolved`, 0 `obsolete`, 126 `still valid`, and 18 `valuable-but-stalled-by-inactivity` classifications after tracker-migration boilerplate was excluded from duplicate similarity scoring. |
| Identity mismatch rejection | A saved blocked receipt records `status: blocked` and a reason beginning `Repository identity mismatch: documentation code-structure inventory=...; expected_upstream='WuJie1010/Facial-Expression-Recognition.Pytorch'`. |
| Malformed JSON handling | Saved audit inputs cover a top-level JSON array, a null manifest collection, `detailed_pull_requests: null`, `issues: null`, and an all-invalid issue list. These inputs are validation cases, not successful empty repositories. |
| Facial-Expression documentation PR | A saved live receipt records draft PR #1 at `https://github.com/Master-Zero1/Facial-Expression-Recognition.Pytorch/pull/1`, created from commit `70760af8f4d9b671bbe3eb944a2c4c86abdb6031`. Its candidate list contains only `README.md`, `ARCHITECTURE.md`, and `CONTRIBUTING.md`. |
| Pokedex documentation PR | A saved live receipt records draft PR #1 at `https://github.com/Master-Zero1/pokedex/pull/1`, created from commit `39527fb16dedb2041d8e022214ef84bbb403a513`. Its candidate list contains the same three documentation files. |
| PypyJS regression | A later full preview regression against `pypyjs/pypyjs` completed all six pipeline stages at `4532320849881093635075db929240052300a844`; its static inventory found JavaScript and HTML test assets without executing them. |

## Tech stack

### Core package

The published package is named `lazarus-revival` and is currently version
`0.1.0`.

- Python `>=3.10`
- Setuptools build backend
- System Git CLI for cloning
- **Zero required third-party runtime dependencies**: `pyproject.toml`
  declares `dependencies = []`

Core console commands:

```text
lazarus
lazarus-clone
lazarus-diagnose
lazarus-docs
lazarus-triage
lazarus-pr
lazarus-synthesize
```

### Optional local API

The `api` optional dependency group declares:

```text
fastapi >= 0.110
uvicorn >= 0.27
pydantic >= 2.0
```

`lazarus-api` starts the local HTTP application. Its current routes are:

```text
GET  /health
POST /runs
GET  /runs
GET  /runs/{run_id}
POST /runs/{run_id}/cancel
GET  /runs/{run_id}/artifacts
GET  /runs/{run_id}/artifacts/{artifact_path}
```

Run metadata is stored locally with Python’s built-in SQLite support. The API
starts each pipeline run as a separate `lazarus` subprocess, captures logs in
the run directory, and polls for a final receipt.

### Frontend

The frontend is a Vite + React + TypeScript application with Tailwind CSS.

Current direct runtime dependencies are:

```text
react             18.3.1
react-dom         18.3.1
react-markdown     9.1.0
remark-gfm         4.0.1
jspdf              4.2.1
```

Current development dependencies include:

```text
tailwindcss        3.4.17
typescript         5.7.3
vite               8.1.5
@vitejs/plugin-react 6.0.3
```

The dashboard launches, observes, cancels, and reads artifacts from local API
runs. It renders Markdown reports, displays safe generated artifacts, and can
create a searchable client-side Evidence PDF. The browser does not receive
GitHub credentials and does not write directly to GitHub.

## Safety and non-negotiables

Lazarus is deliberately conservative around untrusted repositories.

- **No target-source modification.** It does not modify application source
  files, dependency manifests, tests, CI files, licenses, or configuration.
- **No target-code execution.** Diagnosis and documentation generation use
  static inspection; the pipeline does not install unknown dependencies or run
  the target project.
- **Repository identity is bound.** `clone_repo.py` creates a read-only local
  checkout. The orchestrator validates that checkout and downstream artifacts
  against the expected `owner/repository` identity before continuing.
- **Untrusted content stays data.** README text, code comments, issue bodies,
  and pull-request descriptions are never interpreted as instructions.
- **No automatic live PR from the pipeline.** The orchestrator uses PR preview
  mode and never passes `--execute` or operator approval.
- **A real PR is narrow and explicit.** The execution path requires
  `--execute`, the exact `I_APPROVE_DRAFT_PR` approval token, fork validation,
  expected-upstream validation, and a documentation-only allowlist.
- **No merge operation.** Lazarus creates only draft pull requests when
  explicitly authorized. It never merges them.
- **No arbitrary dashboard file access.** The API checks path containment and
  separately allowlists public generated artifacts. The local clone and `.git`
  metadata are not browser-visible.
- **No hidden success.** Missing, malformed, mismatched, or halted artifacts
  are surfaced as errors, limitations, or partial-report conditions.

## Setup and run locally

### Core pipeline

From PowerShell:

```powershell
git clone https://github.com/Master-Zero1/lazarus.git
cd lazarus

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e .

lazarus --help
```

Run a preview pipeline:

```powershell
lazarus <repository-url> `
  --owner <github-owner> `
  --repo <repository-name> `
  --output-dir .\lazarus_runs\example
```

For example, a read-only preview against the Facial-Expression fixture’s
upstream identity uses:

```powershell
lazarus https://github.com/WuJie1010/Facial-Expression-Recognition.Pytorch.git `
  --owner WuJie1010 `
  --repo Facial-Expression-Recognition.Pytorch `
  --output-dir .\lazarus_runs\facial-preview
```

The orchestrator does not create a live PR. Its PR stage produces a preview
receipt only.

### Optional API

Install the optional API dependencies:

```powershell
python -m pip install -e ".[api]"
lazarus-api
```

The API defaults to `127.0.0.1:8000`.

### Dashboard

In another PowerShell terminal:

```powershell
cd frontend
Copy-Item .env.example .env
npm.cmd install
npm.cmd run dev
```

The dashboard normally appears at `http://localhost:5173`. Set
`VITE_API_BASE_URL` in `frontend/.env` if the API is not available at
`http://localhost:8000`.

## Project structure

```text
lazarus/
├── AGENTS.md
│   Project boundaries and operating instructions
├── LICENSE
├── README.md
├── pyproject.toml
│   Package metadata, optional API dependencies, and console commands
├── directives/
│   Layer 1 source SOPs: diagnosis, documentation, triage, draft PR,
│   synthesis, and full-pipeline orchestration
├── src/
│   └── lazarus/
│       ├── agents/
│       │   Layer 2 orchestration, identity validation, and report synthesis
│       ├── execution/
│       │   Layer 3 deterministic clone, inventory, fetch, parse, and
│       │   guarded draft-PR scripts
│       ├── directives/
│       │   Packaged runtime copies of the SOPs
│       └── api/
│           Optional FastAPI application, SQLite store, and subprocess runner
├── frontend/
│   Vite + React dashboard, API client, report viewer, and Evidence PDF export
├── .codex/skills/
│   Project-specific diagnosis, documentation, triage, and future safe-change
│   guidance
├── test_repos/
│   Local verification clones; ignored by Git
├── output/
│   Generated reports and documentation drafts; ignored by Git
├── .tmp/
│   Disposable verification artifacts; ignored by Git
├── lazarus_runs/
│   Local API run artifacts; ignored by Git
└── lazarus_api.sqlite3
    Local API state; ignored by Git
```

## Known limitations

These are deliberate current boundaries, not hidden caveats.

- The API has **no authentication** and permissive CORS for local demo use.
  It listens on loopback by default and must not be exposed publicly until
  authentication, authorization, and stricter deployment controls exist.
- Background pipeline monitoring is in-process and local. It is not a
  distributed job queue. Restart recovery is best-effort and marks a run as an
  error if its final state cannot be determined.
- Lazarus does not execute target code, install dependencies, or run target
  tests. It can report static evidence, not prove current runtime compatibility.
- CI configuration presence is not proof that CI currently works.
- Triage conclusions are limited to the GitHub metadata actually fetched. If
  comment bodies, review threads, or pull-request diffs were not fetched, the
  reports state that limitation.
- Automated source-code modification is intentionally deferred. The
  project’s safe-code-modification guidance exists specifically because that
  capability is not part of the current release.

## Contributing

Lazarus should remain more trustworthy than it is aggressive.

Before contributing:

1. read `AGENTS.md`;
2. keep directives, orchestration, and deterministic execution separate;
3. preserve read-only behavior against target repositories;
4. add negative tests for malformed input, identity mismatch, and unsafe paths;
5. state uncertainty in reports rather than filling gaps with inference; and
6. keep root directives aligned with packaged runtime copies.

For frontend-specific setup and manual checks, read `frontend/README.md`.

---

<div align="center">

Built by <a href="https://github.com/Master-Zero1">Master-Zero1</a><br />
Built with Codex and GPT-5.6 as an engineering collaborator<br />
Hackathon entry: OpenAI Build Week

</div>
