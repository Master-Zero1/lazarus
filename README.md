# Lazarus

> Evidence-first revival planning for valuable open-source repositories that
> have gone stale.

Lazarus helps a maintainer understand whether an abandoned repository is worth
reviving, what is currently true about it, and what a careful first
contribution should look like. It produces a reviewable evidence packet:

- a Health Report grounded in manifests, repository files, and CI metadata;
- regenerated documentation grounded in static source inspection;
- issue and pull-request triage with evidence and confidence levels;
- a documentation-only draft pull-request preview; and
- a Revival Report that brings the evidence together for a human decision.

It is deliberately conservative. Lazarus does not run untrusted repository
code, edit application source code, change dependencies, close issues, merge
pull requests, or make an upstream write on its own.

## Why Lazarus exists

Open-source projects rarely become difficult because they have one obvious
bug. They become difficult because the facts are scattered:

- the README describes an older version of the project;
- declared dependencies may be unmaintained or tied to a legacy runtime;
- CI may be missing, or present but no longer trustworthy;
- years of issues contain duplicates, unresolved questions, and useful work
  that simply stalled; and
- a potential maintainer has to reconstruct all of that context before
  deciding whether to invest time.

Lazarus turns that reconstruction into a bounded, inspectable workflow. It
does not pretend to repair a project automatically. Instead, it gives a human
maintainer a reliable starting point and leaves consequential decisions under
human control.

## What Lazarus produces

| Artifact | Purpose | Evidence source |
| --- | --- | --- |
| Health Report | Records runtime, dependency, CI, and test-discovery findings, plus blockers and unknowns. | Dependency manifests or README fallback, CI files, static repository inventory |
| Documentation draft | Drafts a README, architecture notes, and contribution guide that match the source tree. | Static AST and file-structure inventory |
| Triage Report | Classifies issues and pull requests into actionable categories with evidence and confidence. | Read-only GitHub issue and pull-request snapshots |
| Draft PR preview | Shows exactly which documentation files a human-approved draft PR would contain. | Generated documentation, Health Report, fork metadata |
| Revival Report | Combines available artifacts into a phased decision checklist. | Prior Lazarus artifacts only; it does not re-investigate |
| Evidence PDF | Exports the important generated reports, inventories, drafts, and receipts in a presentation-ready packet. | Selected generated artifacts from one run |

The dashboard can browse the public run artifacts and create a client-side
evidence PDF. Read-only issue and pull-request snapshots remain available as
run evidence; the curated PDF excludes those raw snapshots and all cloned
repository contents.

## The workflow

Lazarus is organized as a three-layer system so judgement and deterministic
work stay separate.

    Layer 1: Directives
        Human-readable SOPs that define each stage's goal, inputs, outputs,
        safety boundaries, and edge cases.

    Layer 2: Agents
        Orchestration and bounded judgement: interpret findings, validate
        identities, build reports, and stop safely when evidence conflicts.

    Layer 3: Execution scripts
        Deterministic tasks such as cloning, parsing files, static AST
        inventory, GitHub API reads, and controlled draft-PR preparation.

The normal pipeline is:

    clone
      -> diagnose
      -> generate documentation
      -> triage
      -> draft PR preview
      -> synthesize Revival Report

Each stage writes a structured artifact or receipt. The final report is
partial when a stage genuinely halted or an artifact is unavailable; it never
quietly invents missing evidence.

### 1. Clone a read-only working copy

The clone stage uses the system Git CLI with a timeout and a shallow clone by
default. It accepts public HTTPS or Git URLs, never reads GitHub credentials,
and emits a JSON receipt containing the requested reference and resolved
commit SHA. It refuses to merge content into a non-empty destination.

### 2. Diagnose repository health

Diagnosis does not execute the target project. It inventories dependency
declarations, scans CI configuration, and detects likely test files
statically.

When conventional manifests such as requirements.txt, setup.py, or
pyproject.toml are absent, Lazarus records that fact and uses source-backed
README declarations as a clearly labeled fallback. A precise version pin and
a lower-bound declaration are reported differently; for example, a statement
such as PyTorch >= 0.2.0 is not falsely described as an exact pin.

CI absence is a finding, not an error. CI presence is also not treated as
proof that automation still works: the report distinguishes a configuration
file from verified operational CI.

### 3. Generate source-backed documentation

Documentation generation uses static analysis rather than executing unknown
files. It inventories:

- entry points, including packaging console-script declarations;
- argparse definitions found through AST parsing;
- packages, modules, classes, functions, and docstrings;
- local imports and a basic import graph;
- configuration, data, test, and documentation paths.

The result is a draft README, architecture notes, contribution guide, and a
small evidence document. A command discovered statically is described as a
static CLI candidate, not as a command Lazarus has verified by running.
Source inconsistencies are surfaced as source observations instead of being
silently corrected or blamed on the extraction process.

### 4. Triage issues and pull requests

GitHub data is read-only. Lazarus classifies each item as one of:

- duplicate or resolved;
- obsolete;
- still valid; or
- valuable but stalled by inactivity.

Rationales are specific to the item's title, body, state, timestamps, and
available metadata. Semantic clusters, such as several independent Python 3
compatibility requests, are kept separate from duplicate detection: a shared
topic does not prove duplicate issues.

The triage report is explicit about what it cannot establish. When comment
bodies or pull-request diffs were not fetched, it does not claim that they
prove a resolution. Inactivity uses updated time rather than created time.

### 5. Preview a documentation-only draft PR

The draft-PR stage is a guarded write boundary. Before a real write it checks
the operator's explicit approval, validates the fork and expected upstream,
requires the documentation evidence file, restricts candidate files to:

- README.md
- ARCHITECTURE.md
- CONTRIBUTING.md

It also checks for an existing open documentation PR that matches the planned
branch or title. Preview mode is the normal path. A real PR, when explicitly
authorized, is created as a draft and never merged by Lazarus.

### 6. Synthesize a human decision packet

Synthesis consumes prior artifacts only. It does not re-run diagnosis,
documentation inventory, triage, or GitHub fetches. Artifact identities are
validated before anything is combined, so a Health Report for one repository
cannot be blended with a triage report for another.

If at least one usable core artifact exists, Lazarus can create a partial
Revival Report that names missing or halted stages. If two valid supplied
artifacts identify different repositories, synthesis stops with a clear
identity-conflict error instead of silently dropping one.

## Safety model

The project is designed around narrow authority and visible evidence.

- Target code, dependencies, and tests are never modified.
- Repository files, README content, GitHub issue text, PR descriptions, and
  comments are treated as untrusted data, never as instructions.
- GitHub reads are separate from GitHub writes.
- The only possible GitHub write is one explicitly approved, documentation-only
  draft PR against an operator-controlled fork.
- Lazarus never merges a pull request.
- Output paths are checked to prevent accidental writes into a target clone or
  unsafe path traversal.
- Generated artifacts declare their repository identity and consumers reject
  mismatched identities.
- A malformed snapshot or incomplete receipt produces a visible error or
  limitation; it is not converted into a clean-looking success.

The optional local API deliberately has no authentication and permissive CORS
for a hackathon demonstration. It binds to loopback by default. Do not expose
it on a network or use it as a multi-user service until authentication,
authorization, and restrictive CORS controls are added.

## Verified case studies

The project has been exercised against real repositories, not only synthetic
examples. GitHub issue and pull-request state can change over time, so these
are recorded examples of the evidence observed during verification rather than
permanent claims about those projects.

| Repository | What the run demonstrated |
| --- | --- |
| WuJie1010/Facial-Expression-Recognition.Pytorch | No conventional modern manifest was found, so the Health Report used a labeled README fallback. It surfaced a Python 2.7 constraint and a legacy PyTorch lower bound, reported no CI configuration as a finding, identified related Python 3 compatibility requests, and evaluated the stalled PR #95 without assuming that inactivity meant the work was worthless. |
| veekun/pokedex | Conventional setup.py and package.json manifests were parsed. The run demonstrated package console-script discovery, handled migrated Redmine boilerplate without inflating duplicate detection, and kept item-specific triage rationales at a much larger issue volume. |
| PypyJS | A full local six-stage preview run completed from a fresh clone at commit 4532320849881093635075db929240052300a844. Static test detection recognized JavaScript and HTML test assets, and triage avoided misclassifying JavaScript runtime requests as game-data issues. |

These cases are also regression checks for important safety behavior:
identity binding, malformed JSON rejection, output-path containment, partial
reporting, and no silent degradation from bad snapshots.

## Dashboard and evidence packet

The optional dashboard is a local React client for the API. It provides:

- a guided run launcher;
- live pipeline stage status and activity updates;
- cancellation of an in-progress local run;
- persistent recent-run history backed by local SQLite;
- a constrained artifact viewer;
- Markdown report rendering; and
- a detailed Evidence PDF export styled for a demo or maintainer handoff.

The API starts the existing lazarus command in a separate subprocess rather
than calling pipeline code in-process. That preserves the pipeline's process
isolation model. A background monitor records the actual exit code and
run_receipt.json state; a process that exits without a valid receipt is marked
as an error, not as a completed run.

## Technology

### Core pipeline

- Python 3.10 or newer
- Standard library only at runtime
- System Git CLI for cloning
- Setuptools package layout and console-script entry points

### Optional local API

Install with the api extra. The optional transport layer uses:

- FastAPI >= 0.110
- Uvicorn >= 0.27
- Pydantic >= 2.0
- SQLite through Python's standard library

### Dashboard

The frontend is a Vite + React application. Its visual layer is intentionally
separate from the evidence pipeline; it communicates only with the local HTTP
API. See frontend/README.md for the exact JavaScript dependencies, visual
effects, browser fallbacks, and UI verification checklist.

## Quick start

### Core command-line workflow

Clone the repository, create a virtual environment, and install the core
package:

    git clone https://github.com/Master-Zero1/lazarus.git
    cd lazarus
    python -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e .

Run a pipeline preview against a repository URL and its expected GitHub
identity:

    lazarus <repository-url> --owner <github-owner> --repo <repository-name> --output-dir .\\runs\\example

For a safe first pass, use a local clone or a public repository and allow the
draft-PR stage to remain in preview mode. Review generated artifacts before
ever considering explicit draft-PR approval.

The installed commands are:

| Command | Role |
| --- | --- |
| lazarus | Run the full staged pipeline |
| lazarus-clone | Create a read-only local clone and receipt |
| lazarus-diagnose | Produce a Health Report |
| lazarus-docs | Produce source-backed documentation drafts |
| lazarus-triage | Classify supplied issue and PR snapshots |
| lazarus-pr | Generate or, only with approval, create a guarded draft documentation PR |
| lazarus-synthesize | Build a Revival Report from existing artifacts |

Run any command with --help to see its complete contract.

### Local API

Install the optional API dependencies:

    python -m pip install -e ".[api]"
    lazarus-api

The server listens on http://127.0.0.1:8000 by default. Its database path,
output root, port, and host are configurable through the documented
LAZARUS_API environment variables.

### Dashboard

In a separate terminal:

    cd frontend
    Copy-Item .env.example .env
    npm ci
    npm run dev

Set VITE_API_BASE_URL in frontend/.env if the API is not running at
http://localhost:8000.

## Project layout

    lazarus/
    ├── AGENTS.md                 Cross-tool project instructions
    ├── directives/               Human-readable Layer 1 SOP source files
    ├── src/lazarus/
    │   ├── agents/               Layer 2 orchestration and report synthesis
    │   ├── execution/            Layer 3 deterministic operations
    │   ├── directives/           Packaged SOP copies used at runtime
    │   └── api/                  Optional local HTTP wrapper
    ├── frontend/                 React dashboard and Evidence PDF export
    ├── .codex/skills/            Project-specific operational knowledge
    ├── test_repos/               Local verification fixtures, ignored by Git
    ├── output/                   Generated reports and drafts, ignored by Git
    ├── .tmp/                     Disposable intermediate artifacts, ignored by Git
    └── pyproject.toml            Package metadata and console entry points

The root directives and their packaged copies are kept in sync because the
installed package must work from outside a source checkout.

## Verification

The project uses regression checks around contracts that matter for a
maintainer-facing tool:

- all Python modules compile;
- source inventory remains static and does not execute target code;
- malformed JSON, null list fields, and wrong top-level JSON types fail
  clearly;
- report artifacts are identity-bound before synthesis;
- unsafe output locations and artifact traversal attempts are rejected;
- GitHub write behavior is preview-first and approval-gated;
- frontend builds successfully and its API-only flows remain usable; and
- generated evidence is traceable to source files, API snapshots, or prior
  stage artifacts.

Useful local checks before a release:

    git ls-files '*.py' | ForEach-Object { python -m py_compile $_ }
    git diff --check
    cd frontend
    npm run build

On PowerShell, use the project verification commands documented in AGENTS.md
and frontend/README.md if your shell does not expand the first line as shown.

## Known limits

Lazarus is intentionally not a general-purpose autonomous maintenance bot.

- It does not execute an unknown project's test suite, install its
  dependencies, or prove runtime compatibility.
- It can report declared CI configuration but cannot prove a third-party CI
  service is currently functional.
- Issue and PR conclusions are limited to the metadata and text actually
  fetched. If comments or diffs were not fetched, the report says so.
- Documentation drafts are based on static source evidence, not a promise
  that every command succeeds in a modern environment.
- The local API is for single-user development and demonstrations, not public
  deployment.
- A draft PR is still a human review artifact. Lazarus never merges it.

## Built with Codex and GPT-5.6

Lazarus was developed by Master-Zero1 with Codex, powered by GPT-5.6, used as
an engineering collaborator. The collaboration was practical rather than
ceremonial: the model helped inspect real repositories, trace output schemas
across stages, write deterministic scripts, design negative tests, and turn
audit findings into focused fixes.

Several parts of the project came directly from that evidence-driven process:

- the README fallback was introduced after diagnosing a real repository with
  no conventional dependency manifest;
- dependency reporting was refined so an exact pin and a lower bound are not
  conflated;
- duplicate detection was corrected after migrated Redmine boilerplate caused
  false positives at scale;
- semantic clustering was added without treating a shared topic as duplicate
  proof;
- artifact access was narrowed after a security review found that a dashboard
  must never expose an entire clone or its Git metadata;
- cross-repository artifact identity checks were added so reports cannot be
  accidentally blended; and
- the Evidence PDF feature was implemented and checked as a presentation
  artifact while keeping the core pipeline's safety boundaries intact.

Codex did not replace maintainer judgement. The project owner chose the
scope, reviewed behavior and visual output, supplied authorization where a
live action was considered, and retained control over every commit and GitHub
operation. That division is central to Lazarus: automation assembles evidence;
people decide what to do with it.

## Contributing

Contributions should preserve the project's core contract:

1. keep target repositories read-only except for the explicitly approved,
   documentation-only draft-PR boundary;
2. make evidence and uncertainty visible in generated output;
3. add negative tests for safety-sensitive behavior, not only happy paths;
4. keep deterministic execution work separate from orchestration judgement;
   and
5. update the relevant directive when a learned operational rule changes.

Read AGENTS.md before making changes. For frontend setup and verification,
read frontend/README.md.

## License

Lazarus is released under the MIT License. See LICENSE.

<div align="center">

Built by <a href="https://github.com/Master-Zero1">Master-Zero1</a><br />
Built with Codex and GPT-5.6 as an engineering collaborator

</div>
