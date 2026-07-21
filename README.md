# Lazarus

Lazarus produces evidence-backed revival materials for stale open-source
repositories: a Health Report, code-derived documentation drafts, issue and
pull-request triage, a documentation-only draft-PR preview, and a Revival
Report for human review.

The core package requires Python 3.10 or later and the system `git` command.
It has no third-party runtime dependencies.

```bash
python -m pip install .
```

The primary command is `lazarus`. Individual stages are available as
`lazarus-diagnose`, `lazarus-docs`, `lazarus-triage`, `lazarus-pr`,
`lazarus-synthesize`, and `lazarus-clone`.

## Pipeline boundaries

Lazarus is read-and-report plus documentation generation. It does not modify
target application source code, dependencies, or test files. A documentation
PR is always a draft, contains reviewed documentation only, and requires
explicit operator approval. Lazarus never merges a pull request.

## Optional local API and dashboard

The optional FastAPI transport and React dashboard are intended for local
development and hackathon demonstrations. Install the API extra before using
`lazarus-api`:

```bash
python -m pip install -e ".[api]"
lazarus-api
```

The dashboard lives in [`frontend/`](frontend/README.md):

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_BASE_URL` in `frontend/.env` when the API is not available at
`http://localhost:8000`.

### Important demo-security limitation

The optional API is deliberately **unauthenticated** and uses permissive CORS
for local frontend development. `lazarus-api` therefore listens on
`127.0.0.1` by default. Do not set `LAZARUS_API_HOST` to a network interface
or deploy it as a multi-user service until authentication, authorization, and
a restrictive CORS policy are added.

## Project layout

- `directives/` — human-readable SOP source files.
- `src/lazarus/agents/` — Layer 2 orchestration.
- `src/lazarus/execution/` — Layer 3 deterministic scripts.
- `src/lazarus/directives/` — packaged runtime copies of the SOPs.
- `src/lazarus/api/` — optional HTTP transport that launches the `lazarus`
  console command in a separate process.
- `frontend/` — the API-only React operations dashboard.

## License

Lazarus is released under the [MIT License](LICENSE).
