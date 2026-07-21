# Lazarus Operations Dashboard

The Lazarus frontend is a single-page React client for the Stage 2 HTTP API.
It never runs the revival pipeline in-process: the browser creates, observes,
cancels, and reads artifacts from API-owned runs.

The current interface uses a light neo-brutalist visual system: warm paper
surfaces, black structural borders, offset shadows, and yellow/blue/red state
accents. It intentionally favors clear operational data over decorative effects.

## Run locally

Install the Lazarus API extra from the repository root, then start the API in
a separate terminal:

```powershell
python -m pip install -e ".[api]"
lazarus-api
```

`lazarus-api` is an intentionally unauthenticated, permissive-CORS local demo
server. It listens on `127.0.0.1:8000` by default. Do not set
`LAZARUS_API_HOST` to a network interface or expose it publicly until
authentication, authorization, and restrictive CORS rules are implemented.

Copy the environment example if the API is not available at the default origin:

```powershell
Copy-Item .env.example .env
# VITE_API_BASE_URL=http://localhost:8000
```

Install and start the frontend:

```powershell
npm.cmd install
npm.cmd run dev
```

Open the Vite URL shown in the terminal, usually `http://localhost:5173`.

## Production build

```powershell
npm.cmd run build
npm.cmd run preview
```

## API behavior

- `POST /runs` starts a new revival from the form.
- `GET /runs` supplies the Recent Runs list.
- `GET /runs/{id}` is polled every two seconds for an active run.
- `POST /runs/{id}/cancel` is used only by the visible Cancel mission control.
- `GET /runs/{id}/artifacts` and its artifact endpoint power the evidence viewer.
- `GET /health` powers the API status indicator.

The frontend displays stage state only when the API provides it. While an
orchestrator run is active but has not written its final receipt, stage labels
remain **receipt pending** rather than fabricating progress.

The browser never receives GitHub credentials or writes directly to GitHub.
It communicates only with the configured Lazarus API origin.

## Visual behavior

- Desktop shows a compact operations rail, main workspace, and real Recent
  Runs sidebar.
- Phone widths use a bottom navigation bar that points only to real sections:
  Start, Run, Pipeline, and Artifacts.
- The major controls are all keyboard focusable and use the same high-contrast
  black/yellow/blue/red interaction system.
- `prefers-reduced-motion` disables the small pipeline and loading animations.
- Space Grotesk is used for display labels, Inter for body text, and IBM Plex
  Mono for run IDs, timestamps, paths, and artifacts.

## Manual check

1. Confirm the API health badge changes between online and unavailable.
2. Submit a valid repository URL, owner, and repository name. Confirm the run
   becomes selected and the live status/pipeline update through polling.
3. Select a past run from Recent Runs and confirm its diagnostics, stages, and
   available artifacts appear.
4. Open a Markdown or JSON artifact and confirm it renders inline; download a
   binary artifact instead.
5. At a phone viewport, confirm the bottom navigation remains available while
   the panels stack vertically.
