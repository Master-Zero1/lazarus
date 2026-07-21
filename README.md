<div align="center">

# LAZARUS

### Turn an abandoned open-source repository into an evidence-backed revival plan — without touching its source code.

**Diagnose the real state. Rebuild honest documentation. Untangle the backlog. Hand a maintainer a safe first move.**

[What it solves](#the-problem) · [What one run creates](#what-happens-in-one-run) · [Verified results](#what-is-actually-verified) · [Run locally](#run-lazarus-locally)

</div>

---

## The one-minute pitch

A repository can be valuable and still be effectively unusable.

Maybe its README is six years old. Maybe it only runs on Python 2.7. Maybe
there are 100 open issues, a half-finished pull request with a useful fix, and
no reliable way for a new maintainer to know what is real, what is stale, and
what should happen next.

**Lazarus turns that uncertainty into a reviewed evidence packet.**

Give it a repository URL and expected GitHub identity. Lazarus creates a
read-only local clone, inspects the code and configuration without executing
untrusted project code, reads the public issue and pull-request backlog, and
produces:

1. a **Health Report** — runtime, dependency, CI, test-discovery, and risk
   findings;
2. a **documentation draft** — a source-backed README, architecture notes,
   and contribution guide;
3. a **Triage Report** — a prioritized backlog with evidence and confidence;
4. a **documentation-only draft-PR preview** — exactly what a maintainer
   could review before any write is allowed; and
5. a **Revival Report** — the practical answer to: *should we revive this,
   and what should a human do first?*

Lazarus is not an autonomous code-repair bot. It is the careful research
assistant a maintainer needs before deciding whether a revival effort is
worth starting.

## The problem

Abandoned repositories are rarely abandoned because every line of code became
worthless. More often, the project lost its map.

| What a new maintainer sees | Why it is a serious problem |
| --- | --- |
| An old README | It may document commands, dependencies, or project structure that no longer match the code. |
| No requirements file, or an old dependency declaration | It is hard to tell whether a compatibility problem is real, guessed, pinned, or only a lower bound. |
| Missing or stale CI | A green-looking configuration file does not prove the checks still run; no CI tells you something important too. |
| Hundreds of old issues | Some are duplicates, some are still real, some are closed without useful resolution evidence, and some contain valuable work that simply went quiet. |
| A pull request with conflicts | It may be obsolete — or it may be the most useful unmerged fix in the repository. |
| A fork and an upstream repository | A careless automation tool can write to the wrong place, leak clone contents, or create duplicate pull requests. |

Today, a person must manually reconstruct all of that context before writing a
single line of code. That is slow, error-prone, and discouraging.

Lazarus makes the first pass repeatable. It collects evidence, names what it
cannot verify, and gives the next human a concrete, reviewable plan instead of
a vague claim that the repository is “dead.”

## What happens in one run

    A Git URL and expected repository identity
                    |
                    v
    1. Read-only clone
                    |
                    v
    2. Health diagnosis
       - manifests, README fallback, CI, static test discovery
                    |
                    v
    3. Source-backed documentation
       - modules, entry points, CLI options, imports, data paths
                    |
                    v
    4. Issue and pull-request triage
       - categories, clusters, evidence, confidence, limitations
                    |
                    v
    5. Documentation PR preview
       - only README.md, ARCHITECTURE.md, CONTRIBUTING.md
                    |
                    v
    6. Revival Report and Evidence PDF
       - a human decision packet, not an automated code change

Every stage leaves a named artifact or receipt behind. If a stage cannot
continue safely, Lazarus halts or produces a clearly marked partial report; it
does not quietly fill gaps with guesses.

### What a maintainer receives

| Deliverable | Plain-language answer it provides |
| --- | --- |
| Health Report | “What is this project actually tied to, and what might block a revival?” |
| Regenerated README | “What does the code appear to do, and what are the real ways into it?” |
| Architecture Notes | “Which packages, modules, data paths, and imports matter?” |
| Contributing Guide | “How can a future contributor approach the project responsibly?” |
| Triage Report | “Which issues are noise, which are still relevant, and which old PRs deserve attention?” |
| Draft-PR preview | “What documentation-only change would be proposed, exactly?” |
| Revival Report | “What should a human decide now, later, and only after more verification?” |
| Evidence PDF | “How can I share the result with judges, maintainers, or teammates in one polished packet?” |

## Why this is different

There are many tools that can scan a repository, summarize a README, or list
issues. Lazarus is built around the gaps between those tools.

| Typical shortcut | Lazarus approach |
| --- | --- |
| “The README says it uses Python 2.7, so that must be true.” | Preserve the source of the claim and distinguish a README fallback from a parsed manifest. |
| “There is a CI file, so CI works.” | Report configuration presence separately from verified operational status. |
| “These three issues contain similar words, so close them as duplicates.” | Separate textual duplicate detection from semantic topic clustering. Similar topic is not duplicate proof. |
| “This pull request is old, so it is obsolete.” | Use its actual state, description, merge metadata, and activity. Classify valuable but stalled work separately. |
| “A report is missing, so call the whole run successful anyway.” | Name missing or halted artifacts and make the final report partial when evidence is incomplete. |
| “The target project is untrusted, but run its setup command to learn more.” | Never execute target code as part of diagnosis or documentation inventory. |
| “Open a helpful PR automatically.” | Preview first; any real write is explicitly approved, fork-validated, documentation-only, and always a draft. |

The result is not just an AI summary. It is an **evidence chain** that a
maintainer can inspect, challenge, and use.

## A real maintainer story

Imagine inheriting an image-recognition repository with a promising model,
several datasets, and years of accumulated questions.

Without Lazarus, you might spend a weekend discovering that:

- the project declares Python 2.7 in its own materials;
- its PyTorch statement is a lower bound, not a precise modern dependency
  lock;
- there is no CI configuration;
- multiple issues independently ask about Python 3 compatibility; and
- an old CPU-support pull request has conflicts but may still contain useful
  work.

With Lazarus, those facts arrive as labeled observations in the Health Report
and Triage Report. The new documentation reflects the actual entry-point
scripts, model package, transforms, and datasets found statically in the
source. The maintainer gets a safe first documentation PR preview and a
decision checklist — without Lazarus changing application code or assuming
that the old project can run.

That is the point: **make an uncertain revival effort legible before anyone
tries to modernize it.**

## The Lazarus pipeline

Lazarus uses a three-layer design. This is not just a folder convention; it
keeps non-deterministic judgement away from low-level operations and makes the
system much easier to audit.

    Layer 1 — Directives
        Markdown SOPs define goals, inputs, outputs, safety rules,
        and edge cases for every stage.

    Layer 2 — Agents
        Orchestration agents read the SOPs, validate artifacts,
        make bounded report-level judgements, and stop safely.

    Layer 3 — Execution
        Narrow deterministic scripts clone, inspect files, parse AST,
        fetch public GitHub metadata, and prepare guarded PR actions.

### Stage 1 — Clone, but do not mutate

The clone script uses the system Git CLI with an explicit timeout. It accepts
public HTTPS or Git URLs, resolves the checked-out commit SHA, and writes a
receipt. It does not use GitHub tokens, create a fork, or merge into an
existing directory.

The orchestrator then verifies that the local clone's identity matches the
expected owner and repository. A clone of the wrong project is not allowed to
flow into a report for the requested project.

### Stage 2 — Diagnose the actual health of the project

Diagnosis is read-only and static. It looks for conventional dependency
manifests such as setup.py, package.json, requirements.txt, and pyproject.toml.

Some old projects do not have a modern manifest at all. In that case, Lazarus
does not pretend there is no dependency information. It can use documented
runtime declarations from the repository README as a **labeled fallback**.
The Health Report says where every finding came from.

The diagnosis stage also:

- distinguishes exact pins from lower-bound-only declarations;
- inventories CI configuration or reports its absence as a finding;
- statically detects likely test files across Python, JavaScript, HTML, and
  common test-directory conventions;
- identifies blockers, human-review priorities, and explicitly unknown facts;
  and
- never installs dependencies or runs the target application.

### Stage 3 — Rebuild documentation from source evidence

Lazarus does not trust a stale README to describe a stale project. Instead, it
performs static code inventory:

- packages, modules, top-level functions, classes, signatures, and docstrings;
- local import relationships;
- configuration, data, documentation, and test paths;
- packaging entry points, including console_scripts declarations; and
- argparse definitions: flags, types, defaults, and help text.

Python AST parsing is used for source inspection. A discovered command is
described as a **static CLI candidate**, not a command that Lazarus has
verified by executing. If source code contains a copy-pasted or inconsistent
help string, the documentation evidence names it as an upstream source
observation instead of silently “fixing” the project.

From that inventory, Lazarus drafts:

- README.md
- ARCHITECTURE.md
- CONTRIBUTING.md
- documentation_evidence.md

These are drafts for human review. They are not silently committed into a
target repository.

### Stage 4 — Triage the backlog without treating it as noise

The triage stage makes read-only GitHub API requests for issue and pull-request
metadata, then classifies each item into one of four categories:

| Category | Meaning |
| --- | --- |
| Duplicate or resolved | There is concrete duplicate or resolution evidence in the available snapshot. |
| Obsolete | The item is no longer relevant based on available evidence. |
| Still valid | The item describes an unresolved concern that remains plausible and actionable. |
| Valuable but stalled by inactivity | The work or proposal appears useful, but activity stopped; it deserves human review rather than dismissal. |

Each rationale is specific to the item rather than a repeated template.
Related requests can be clustered around a shared topic — for example,
compatibility questions — but that relationship is not treated as proof that
they are duplicates.

The report also says what it did not inspect. If comment bodies, review
threads, or PR diffs were not fetched, it will not claim those sources prove a
resolution. Inactivity is based on last update time, not creation date.

### Stage 5 — Prepare one safe documentation PR

The only optional write path is intentionally narrow.

Before anything can be created, Lazarus requires explicit operator approval
and verifies the operator-controlled fork against its expected upstream. It
also checks for an existing matching open documentation PR and validates that
the candidate set contains only:

    README.md
    ARCHITECTURE.md
    CONTRIBUTING.md

Preview mode is the default. A real pull request, when approved, is always a
draft. Lazarus never merges a pull request and never changes target
application code, dependencies, or tests.

### Stage 6 — Build the Revival Report

The synthesis stage only reads earlier Lazarus artifacts. It does not perform
a new investigation just to make the final report look complete.

It checks repository identity on every artifact before combining them. A
Facial-Expression Health Report and a Pokedex Triage Report, for example, are
rejected as an identity conflict rather than silently blended.

If some valid core artifacts are available, Lazarus can write a **partial
Revival Report** that clearly names the missing or halted stages. If none are
usable, it stops rather than manufacturing a conclusion.

## What is actually verified

Lazarus has been exercised against real open-source repositories and targeted
negative cases. GitHub state changes over time, so repository findings below
describe what was observed during verification, not permanent statements about
those projects.

| Case | Verified result |
| --- | --- |
| Facial-Expression-Recognition.Pytorch: dependency diagnosis | The repository had no conventional modern manifest. Lazarus used a labeled README fallback and surfaced the real Python 2.7 constraint and legacy PyTorch >= 0.2.0 lower bound without falsely calling the lower bound an exact pin. |
| Facial-Expression-Recognition.Pytorch: CI diagnosis | No CI configuration was detected. The absence appeared as a structured finding, not an empty result or an execution error. |
| Facial-Expression-Recognition.Pytorch: issue cluster | Related Python 3 compatibility issues were connected as a semantic cluster and cross-referenced to the Python 2.7 Health Report finding without being falsely declared duplicates. |
| Facial-Expression-Recognition.Pytorch: old PR | PR #95 was evaluated using available metadata and was not dismissed merely because it was inactive or conflicted. |
| Pokedex: conventional manifests | Lazarus parsed the real setup.py and package.json rather than falling back to README text. It also detected a packaging console-script entry point, not only argparse scripts. |
| Pokedex: duplicate-resistance test | Migrated Redmine header boilerplate had inflated text-overlap scoring. Lazarus was corrected to discount that structural boilerplate before classifying semantic content. |
| Pokedex: larger backlog | The corrected run retained item-specific rationale at scale, with 127 still-valid items and 18 valuable-but-stalled items rather than collapsing large portions of the backlog into false duplicates. |
| PypyJS: full preview pipeline | A fresh local clone completed all six stages in preview mode at commit 4532320849881093635075db929240052300a844. |
| PypyJS: static test detection | The static inventory recognized lib/tests/index.html and src/tests/tests.js, demonstrating that test discovery is not limited to Python test filenames. |
| PypyJS: language-aware triage | JavaScript runtime requests were not misclassified as game-data issues; the completed report remained internally consistent. |
| Cross-repository safety | Supplying two individually valid artifacts from different repositories causes synthesis to reject the identity conflict rather than silently omit one or produce a blended report. |
| Malformed-data safety | Top-level JSON arrays, null list fields, and all-non-object snapshot entries produce clear validation failures or visible limitations instead of a clean-looking empty success report. |
| Artifact-browser safety | The dashboard serves only a bounded set of Lazarus-generated reports, receipts, logs, and evidence directories. It does not expose the local clone or its Git metadata. |

### What a successful full preview proves

A completed preview proves that Lazarus:

- cloned and identified the requested repository;
- created each expected report artifact;
- kept generated artifacts associated with the right repository;
- completed documentation generation and triage without executing target code;
- produced a guarded draft-PR preview instead of a surprise write; and
- synthesized the generated evidence into a Revival Report.

It does **not** prove that the target project builds on a modern machine, that
every issue is correctly resolved, or that an old CI service still works.
Those are human follow-up decisions and are stated as such in the reports.

## Dashboard and Evidence PDF

Lazarus includes an optional local dashboard for a hackathon demonstration and
maintainer handoff.

The dashboard lets a user:

1. launch a run by submitting a repository URL, owner, and repository name;
2. watch the six stages update in a live pipeline view;
3. inspect recent runs stored in a local SQLite database;
4. browse the reports, receipts, generated documentation, and safe logs;
5. cancel an in-progress local run; and
6. download a styled, detailed Evidence PDF containing the most important
   reports, inventories, documentation drafts, and receipts.

The dashboard is a client of the local HTTP API only. The API starts the
existing lazarus command as a separate subprocess, preserving pipeline
isolation. It monitors the process and the final run receipt: a process that
exits without a valid receipt becomes an error, never a false “completed”
status.

For safety, the browser cannot fetch arbitrary files from a run directory.
Path traversal, symbolic-link escapes, the clone checkout, and its .git
metadata are excluded from the public artifact surface.

### Important demo limitation

The optional API is intentionally unauthenticated and uses permissive CORS for
local development. It listens on 127.0.0.1 by default. Do not expose it to a
network or deploy it as a shared service until authentication, authorization,
and restrictive CORS rules are added.

## Safety is a product feature

Lazarus handles untrusted repositories, so its boundaries are explicit.

| Rule | How Lazarus enforces it |
| --- | --- |
| Do not execute unknown repository code | Diagnosis and documentation inventory use static file and AST inspection. |
| Do not confuse untrusted text with instructions | README text, code comments, issue bodies, and PR descriptions stay data. They do not control tool calls or report format. |
| Do not write to the wrong repository | Clone identity and all report-artifact identities are checked against expected ownership information. |
| Do not create a surprise PR | The PR stage is preview-first and needs real operator approval for an actual draft. |
| Do not write source code | The only allowlisted PR candidates are three documentation files. |
| Do not merge | No merge endpoint or merge operation exists in the pipeline. |
| Do not hide missing evidence | Unknowns, limitations, malformed inputs, partial results, and halted stages are written into output. |
| Do not expose a local clone through the dashboard | The API checks path containment and separately allowlists public artifact paths. |

## Technology

### Core pipeline

- Python 3.10+
- Python standard library only for the core runtime
- Git CLI for read-only cloning
- Setuptools src-layout package
- Console commands for the full pipeline and individual stages

### Optional API

The local transport layer is installed separately with the api extra:

- FastAPI >= 0.110
- Uvicorn >= 0.27
- Pydantic >= 2.0
- SQLite through Python's standard library

### Frontend

The dashboard is a Vite + React application. It is deliberately separate from
the core pipeline and speaks only to the local API. The visual design includes
a mission-control interface, a safe ambient visual fallback, motion-reduction
support, Markdown report rendering, artifact browsing, and client-side PDF
generation.

See frontend/README.md for exact frontend dependencies, browser fallback
details, and its manual verification checklist.

## Run Lazarus locally

### 1. Install the core pipeline

From PowerShell:

    git clone https://github.com/Master-Zero1/lazarus.git
    cd lazarus
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    python -m pip install -e .

The core package requires Python 3.10 or later and the system Git CLI. It has
no mandatory third-party runtime dependencies.

### 2. Run a safe pipeline preview

Use a repository URL and the identity you expect that URL to represent:

    lazarus <repository-url> --owner <github-owner> --repo <repository-name> --output-dir .\runs\example

For an initial run, keep the documentation-PR stage in its preview behavior.
Review the generated materials before ever considering an approved draft PR.

Installed commands:

| Command | What it does |
| --- | --- |
| lazarus | Runs the full ordered pipeline. |
| lazarus-clone | Creates a read-only clone and JSON receipt. |
| lazarus-diagnose | Produces a Health Report from deterministic findings. |
| lazarus-docs | Produces source-backed documentation drafts. |
| lazarus-triage | Classifies supplied issue and PR snapshots. |
| lazarus-pr | Previews, or with approval creates, a guarded documentation draft PR. |
| lazarus-synthesize | Produces a Revival Report from prior artifacts only. |

Use --help on any command for its complete argument contract.

### 3. Start the local API

Install the optional API extra:

    python -m pip install -e ".[api]"
    lazarus-api

The API listens on http://127.0.0.1:8000 by default. The database path,
output root, port, and host can be set with the documented LAZARUS_API
environment variables.

### 4. Start the dashboard

Open another terminal:

    cd frontend
    Copy-Item .env.example .env
    npm ci
    npm run dev

Set VITE_API_BASE_URL in frontend/.env if the API is not running on
http://localhost:8000.

## Project structure

    lazarus/
    ├── AGENTS.md
    │   Project boundaries, workflow rules, and cross-tool instructions
    │
    ├── directives/
    │   Human-readable Layer 1 SOP source files
    │
    ├── src/lazarus/
    │   ├── agents/
    │   │   Layer 2: diagnosis, documentation, triage, PR, synthesis,
    │   │   and full-pipeline orchestration
    │   ├── execution/
    │   │   Layer 3: deterministic clone, inventory, parsing, GitHub read,
    │   │   dependency, CI, and PR-preparation scripts
    │   ├── directives/
    │   │   Packaged SOP copies used when Lazarus is installed
    │   └── api/
    │       Optional FastAPI transport, SQLite store, and subprocess runner
    │
    ├── frontend/
    │   React dashboard, API client, visual components, and Evidence PDF export
    │
    ├── .codex/skills/
    │   Project-specific lessons for repository diagnosis, documentation,
    │   issue triage, and future safe code modification
    │
    ├── test_repos/
    │   Local verification fixtures; ignored by Git
    ├── output/
    │   Generated reports and documentation drafts; ignored by Git
    ├── .tmp/
    │   Disposable intermediate artifacts; ignored by Git
    │
    ├── pyproject.toml
    │   Package metadata, optional API extra, and console entry points
    └── README.md

The root directives and packaged directives are intentionally kept aligned so
an editable installation and an installed wheel use the same stage rules.

## How Codex and GPT-5.6 assisted this project

Lazarus was built by Master-Zero1 with Codex, powered by GPT-5.6, as an
engineering collaborator.

That collaboration was not “ask an AI to generate a repository and hope it
works.” The project was built stage by stage against real repositories, with
the next capability added only after the previous one had been checked. Codex
helped with code inspection, deterministic implementation, threat modeling,
negative testing, report design, API and dashboard integration, and
documentation.

Some concrete examples:

- A real repository without requirements.txt, setup.py, or pyproject.toml led
  to the README-fallback design. The output now labels that fallback instead
  of pretending it is a parsed manifest.
- A real lower-bound PyTorch statement led to the pin-versus-lower-bound rule,
  preventing misleading dependency language.
- A large Pokedex issue set exposed false duplicate matches from shared
  “migrated from Redmine” boilerplate. The overlap analysis was corrected to
  score meaningful content rather than structural headers.
- Related Python 3 requests showed why semantic clustering must remain
  separate from duplicate detection.
- Artifact-security review found that a dashboard must not browse an entire
  clone directory. The API now uses both containment checks and a separate
  allowlist.
- Cross-repository synthesis tests ensured that two valid artifacts from
  different projects fail clearly instead of producing a believable but
  invalid combined report.
- The Evidence PDF was added as a reviewable demo artifact while preserving
  the core pipeline's read-only and approval-gated boundaries.

The human owner remained in control of scope, live-action authorization,
review, visual decisions, and every commit. Codex supplied acceleration and
rigorous iteration; it did not become an autonomous maintainer with authority
to alter somebody else's project.

## Known limits

Lazarus is deliberately useful before a revival, not a replacement for the
revival itself.

- It does not install unknown dependencies or run an unknown repository's
  tests, so it cannot prove runtime compatibility.
- It can report declared CI files but cannot prove that a third-party CI
  platform still works.
- Triage conclusions are limited to the API data actually fetched. If comment
  bodies or PR diffs are absent, that limitation is named.
- Generated documentation is source-backed, not a guarantee that every
  command works on a current operating system.
- The API and dashboard are local demo tooling, not a public multi-user
  service.
- The only possible PR is documentation-only, explicitly authorized, and
  always a draft. Lazarus never merges.

## Contributing

The project values trustworthy boundaries more than flashy automation.

Before contributing:

1. read AGENTS.md;
2. keep target-repository changes out of scope unless a future, separately
   designed safe-modification capability is approved;
3. preserve the separation between directives, orchestration, and
   deterministic execution;
4. test negative and malformed-input cases alongside the happy path;
5. make uncertainty visible in reports rather than filling it with inference;
   and
6. keep the root and packaged copies of any changed directive aligned.

For dashboard-specific setup, visual effects, and frontend checks, read
frontend/README.md.

## License

Lazarus is released under the MIT License. See LICENSE.

---

<div align="center">

Built by <a href="https://github.com/Master-Zero1">Master-Zero1</a><br />
Built with Codex and GPT-5.6 as an engineering collaborator

</div>
