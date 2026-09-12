# Contributor setup and local storage

This guide reproduces the current upload, replay, and live-transcription foundation: React dashboard, FastAPI service, PostgreSQL metadata, validated MP4 storage, manual camera alignment, synchronized playback, cutoff-gated segment release, and xAI Grok Speech-to-Text per camera feed. It does not include observations, sitreps, or reconstruction generation.

Read [README.MD](./README.MD) and [Bodycam MVP Spec.md](./Bodycam%20MVP%20Spec.md) before extending the system. They define the domain rules and deferred scope.

## Required dependencies

Use the versions locked in `package-lock.json` and `apps/api/pyproject.toml`; do not substitute unpinned packages when reproducing a contributor environment.

| Dependency | Required version | Why it is needed |
| --- | --- | --- |
| Git | Current stable release | Clone and contribute to the repository |
| Node.js | 22.12 or newer | React/Vite dashboard, tests, local media tools |
| npm | Bundled with the selected Node.js version | Install the locked JavaScript dependency tree |
| Python | 3.12 | FastAPI API, migrations, and API tests |
| Docker Engine + Docker Compose plugin | Current stable release | Recommended fully isolated local stack |
| Google Chrome | Current stable release | Browser regression tests outside Docker |

The API image installs FFmpeg itself. For native development, `npm ci` installs pinned `ffmpeg-static` and `ffprobe-static`; the project scripts pass those binaries to the API. A separate FFmpeg installation is optional unless you override `FFMPEG_BINARY` or `FFPROBE_BINARY`.

Python runtime dependencies are pinned in `apps/api/pyproject.toml`: FastAPI 0.135.1, Starlette 1.6.0, Uvicorn 0.41.0, SQLAlchemy 2.0.48, Alembic 1.18.4, Psycopg 3.3.3, and python-multipart 0.0.22. Test extras add pytest 9.0.2 and httpx 0.28.1. The browser client is React 19 with Vite, TypeScript, TanStack Query, Zustand, Vitest, and ESLint under `apps/web`.

## Recommended: Docker Compose

This is the closest replication of the project stack because it supplies PostgreSQL 17, FFmpeg, FFprobe, migrations, API, and the production-served dashboard.

```sh
git clone <repository-url> BOP
cd BOP
cp .env.example .env
docker compose up --build
```

Open [http://localhost:5173](http://localhost:5173) for the dashboard and [http://localhost:8000/docs](http://localhost:8000/docs) for API documentation. The API waits for PostgreSQL, then applies Alembic migrations before listening.

Use this health check after the stack starts:

```sh
curl --fail http://localhost:8000/api/health
```

Stop the services while keeping their data with:

```sh
docker compose down
```

`docker compose down -v` removes the project database and all uploaded recordings. Treat it as a local data reset, not a routine stop command.

## Native development

Use native development when changing the frontend or API with live reload. Docker is not required, but native PostgreSQL and Docker Compose storage do not share data.

```sh
git clone <repository-url> BOP
cd BOP
cp .env.example .env
python3.12 -m venv .venv
.venv/bin/python -m pip install -e 'apps/api[test]'
npm ci
```

Start these in three terminals:

```sh
npm run dev:db
```

```sh
npm run dev:api
```

```sh
npm run dev
```

The native database listens on `127.0.0.1:5432`; the API listens on `127.0.0.1:8000`; Vite listens on `127.0.0.1:5173`. The Vite development server proxies `/api` to the API. Set `API_PROXY_TARGET` only when the API is intentionally running elsewhere.

`npm run dev:db` uses the project-local embedded PostgreSQL runtime. It keeps its cluster under `.data/postgres`; stop it with `Ctrl+C`. If Docker Compose is using port 5432, stop the Compose database before starting this command.

## Per-project backend storage

Each checkout owns its own storage. Do not copy another contributor's `.data` directory or Docker volumes into your checkout unless you intentionally need their local test data; they can contain uploaded recordings and incident metadata.

| Runtime | PostgreSQL storage | Uploaded recording storage | Notes |
| --- | --- | --- | --- |
| Docker Compose | Named volume `postgres_data` | Named volume `media_data`, mounted at `/data/media` in the API | Docker prefixes volume names with the Compose project name, normally the checkout directory name. |
| Native development | `.data/postgres` | `.data/media` | Both paths are ignored by Git and persist across ordinary restarts. |
| API tests | Temporary SQLite database by default | Temporary media directory | Deleted after each test. With `TEST_DATABASE_URL`, tests create and remove a random PostgreSQL schema per test. |
| Browser tests | The running local PostgreSQL database | The running local media store | Browser tests create `E2E` incidents and real synthetic MP4s; their temporary fixture files are removed, but created incident records remain available for inspection. |

The backend stores only media metadata and an internally generated storage key in PostgreSQL. Video bytes are stored outside the database. Never construct a media path from a user-supplied filename; the API already generates opaque keys under its configured media root.

The current PostgreSQL schema has four tables:

| Table | Stores |
| --- | --- |
| `incidents` | Title, user context, creation time, and active playback run |
| `recordings` | Camera label, validated original filename, duration, start offset, size, and media storage key |
| `playback_runs` | Run ID, state, backend time anchor, position, and revision for stale-command protection |
| `transcript_segments` | Per-run, per-recording released window with status, provider text, word timestamps, and error text |

Alembic migrations live in `apps/api/migrations`. Never edit an applied migration. Create a new migration for schema changes and verify it against a new local database before opening a pull request.

## Environment configuration

Copy `.env.example` to `.env` for local development. `.env` is ignored by Git.

| Variable | Native default | Compose value | Purpose |
| --- | --- | --- | --- |
| `POSTGRES_DB` | `bop` | `bop` | Database name used by Compose and `dev:db` |
| `POSTGRES_USER` | `bop` | `bop` | Local development database user |
| `POSTGRES_PASSWORD` | `bop_local` | `bop_local` | Local development database password; replace for non-local deployments |
| `DATABASE_URL` | `postgresql+psycopg://bop:bop_local@127.0.0.1:5432/bop` | Generated with host `db` | API database connection string |
| `MEDIA_ROOT` | `../../.data/media` when the API runs from `apps/api` | `/data/media` | Directory holding validated source recordings |
| `MAX_UPLOAD_BYTES` | `536870912` | `536870912` | Per-recording upload limit (512 MiB) |
| `MEDIA_VALIDATION_TIMEOUT_SECONDS` | `120` | `120` | Total FFprobe and FFmpeg validation deadline |
| `FFMPEG_BINARY` | npm-installed FFmpeg path injected by project scripts | `ffmpeg` | Optional override for FFmpeg |
| `FFPROBE_BINARY` | npm-installed FFprobe path injected by project scripts | `ffprobe` | Optional override for FFprobe |
| `XAI_API_KEY` | Empty by default | Passed through from host `.env` | Enables Grok Speech-to-Text; leave empty to disable live transcription |
| `SEGMENT_SECONDS` | `10` | `10` | Duration of each released transcription window |

Do not commit `.env`, uploaded recordings, local media folders, database folders, credentials, or real incident material. The repository's `.gitignore` excludes these local artifacts.

## Media and playback contract

The current foundation accepts an MP4 only when it has exactly one 8-bit H.264 4:2:0 video track and no more than one AAC audio track. Silent MP4s are accepted. FFprobe reads metadata and FFmpeg fully decodes the recording before it becomes available. Validation errors are shown to the contributor in the UI and no recording row is created.

The backend owns the incident clock. A camera with a start offset of `2` begins at incident second 2, and its local media time is `incident time - 2`. At least two valid recordings are required to play. Upload and alignment changes are allowed only at the start of a paused run; restart creates a new run at zero. Do not bypass these guards from the client.

## Verification before a pull request

Run the following after changing the foundation:

```sh
npm run typecheck
npm run lint
npm run test
npm run test:api
npm run build
```

For API tests against PostgreSQL rather than their isolated SQLite default:

```sh
TEST_DATABASE_URL=postgresql+psycopg://bop:bop_local@127.0.0.1:5432/bop npm run test:api
```

With the native API and web server already running, execute browser checks:

```sh
npm run test:e2e
```

Browser tests require Chrome. They cover real upload, alignment, synchronized play/pause/resume/restart, completed feeds, invalid media, durable reload, and lost-connection recovery.

The workflow in `.github/workflows/ci.yml` runs the same checks in GitHub Actions with PostgreSQL 17. Contributors should ensure it passes before merging.

## Resetting only your local development data

Use one of these only when you deliberately want to remove your local incidents and uploaded recordings:

```sh
docker compose down -v
```

For native development, stop `npm run dev:db` first, then remove the specific `.data/postgres` and `.data/media` directories in your own checkout. This is irreversible for local data. Never use either reset approach against a shared or production environment.

## Current boundary for contributors

The present milestone ends with durable upload, alignment, synchronized replay, and per-camera live transcription (xAI Grok). Keep map-location content, statement reconstruction, sitrep items, event history, evidence review, observation extraction, Celery/Redis workers, and SSE out of contributions unless the scope is explicitly expanded. Analysis panels intentionally use empty states until source-linked observations exist.

## Attaching the xAI API key

The API calls Grok STT server-side only; the frontend never sees the key.

1. Create a key in the [xAI console](https://console.x.ai/).
2. Put it in the gitignored project `.env` (or export in your shell for one-off sessions):

```sh
XAI_API_KEY=xai-...your-key...
```

3. Docker Compose passes it through automatically. Native `npm run dev:api` picks up whatever is in the environment the shell inherits — set it in your shell or a `.env` loader you already use.
4. Confirm with `curl http://127.0.0.1:8000/api/health` — `transcription_configured: true` in the response means the API can reach Grok. If it is `false`, transcripts still show queued/empty segments as the pipeline exercises, but no live text will appear.
5. Do not paste real keys into commits, chat, Playwright fixtures, browser code, `VITE_*` variables, or logs. Rotate the key in the xAI console if that happens.
