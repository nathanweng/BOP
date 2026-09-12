# Bodycam Situation Report

This repository is the working specification and implementation home for a web application that turns synchronized, prerecorded body-camera recordings into a continuously updated, source-linked situation report (sitrep).

The product is an incident-review workspace for supervisors or arriving responders. It must make clear what is known, what changed, what is disputed, and what remains unknown. Audio speech is the primary analysis input. Video is retained for playback and evidence review; continuous visual analysis is outside the MVP.

The authoritative product requirements are in [Bodycam MVP Spec.md](./Bodycam%20MVP%20Spec.md). Read this README and the specification before changing the architecture or adding a feature.

For reproducible contributor setup, exact runtime dependencies, backend storage locations, environment variables, and verification commands, see [CONTRIBUTOR_SETUP.md](./CONTRIBUTOR_SETUP.md).

## Project status

The current milestone extends the upload and replay foundation with per-camera live transcription and OpenRouter event histories. The full MVP requirements below describe later work as well; they are not claims that the full observation pipeline is implemented.

- **Complete:** persistent incident creation, MP4 upload and validation, manual camera alignment, backend-owned playback runs, synchronized browser video, cutoff-gated segment release, FFmpeg audio extraction per segment, xAI Grok batch Speech-to-Text (behind a provider interface), and a live-updating transcript under each camera feed. Regression tests cover eligibility, restart isolation, silent recordings, provider failure, and configuration health.
- **Needs verification:** Docker image builds and Compose startup (Docker is unavailable on the development machine); the CI workflow has not yet run on GitHub with the new schema. End-to-end verification with a real `XAI_API_KEY` and staged multi-camera footage is pending.
- **Deferred:** observations, cross-camera grouping, sitrep generation, reconstruction, evidence review/corrections, Celery/Redis workers, and SSE. No observation-schema placeholders are being implemented in this milestone.
- **Specified next stage:** a run-complete, source-linked incident knowledge map is defined in [Mind Map Stage Spec.md](./Mind%20Map%20Stage%20Spec.md). It adds a proposed Connections tab only after every expected transcript window has completed successfully, then supports timeline-cutoff views of claims, witnesses, evidence, events, locations, updates, and conflicts. This is a product and architecture specification; it is not implemented yet.
- **Interface:** basic functional styling; map/location, reconstruction, sitrep, and evidence areas remain empty until supported data and processing exist. Each camera feed shows a live transcript panel. Event history now generates timestamp/title rows from completed transcripts and opens the source video at the corresponding segment start.

### Event history (OpenRouter)

Set `OPENROUTER_API_KEY` in the root `.env`, then restart the API. `OPENROUTER_MODEL` defaults to `openai/gpt-4o-mini` and can be changed to an OpenRouter model supporting JSON output. Both variables are passed to the API by Compose; the key is never sent to the browser. The integration uses the [OpenRouter chat completions API](https://openrouter.ai/docs/quickstart).

After recordings have produced transcripts, click **Generate / update history**. The backend sends the complete released transcript history and the saved log to OpenRouter, validates source references and status changes, and persists the history per run. Existing timestamp and title fields are immutable. The model should add every distinct material event and meaningful clarification. Later evidence can mark a row yellow when it shows an earlier entry was incomplete, misleading, or superseded, or red when it explicitly contradicts it. Each colored row includes a clickable `Clarified at` or `Contradicted at` timestamp that opens the later source. Generation failures retain the last saved history; restart displays an empty history for the new run.

The table contains only **Timestamp** and **Event title**. Timestamps are incident-clock source-segment starts (typically 10-second granularity), not inferred exact event times. Clicking one opens the matching camera in a source player, accounting for camera alignment, without changing the shared replay clock. Extraction is based on speech, so `XAI_API_KEY` is still needed for the existing transcription pipeline. No visual event detection or full observation/review workflow is added.

Requests are limited to 120,000 transcript characters and 500 generated events. Larger histories require future chunking. Real OpenRouter output quality and browser playback verification remain pending until configured with a key and recordings.

Verified September 12, 2026: TypeScript checks, ESLint, production build, seven frontend unit tests, and all 37 SQLite API tests pass. Three event-history tests cover provider source validation, aligned timestamps, missing configuration, persistence, failure retention, cutoff filtering, and restart isolation. No live OpenRouter request was made.

### Foundation boundaries

Create an incident, upload one or more MP4 recordings, set when each starts on the incident clock, and replay them together. A start offset of `2` means the camera begins at incident second 2; its local media time is `incident time - 2`. Offsets range from 0 to 86,400 seconds. The duration of the incident is the latest recording end (`offset + duration`).

Uploads must contain one 8-bit H.264 video track with 4:2:0 color and at most one AAC audio track. Silent recordings are accepted. FFprobe reads metadata and FFmpeg decodes the media to validate it; no audio is extracted for analysis. The default limit is 512 MiB per file and validation has a timeout. A recording becomes available only after validation and database storage succeed.

The backend persists a run ID, revision, time anchor, and playback position. Browser clocks follow that state; stale playback commands return HTTP 409. At least one recording is needed to play. Uploading or editing alignment requires a paused run at zero: restart first after replay has begun. Restart creates a new run, preserves earlier run records, and returns the incident to zero. Resume continues the existing run. Closing the browser does not itself pause the backend clock.

## Recommended stack

Use a small monorepo with a browser client and API. The media-analysis worker and analysis-specific dependencies are planned for a later milestone:

| Area | Choice | Purpose |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite | Interactive incident dashboard without unnecessary server-rendering complexity |
| UI | Plain CSS now; Tailwind CSS and shadcn/ui later if needed | Functional panels, status labels, and accessible controls |
| Client state | TanStack Query and Zustand | Server data/cache plus local playback, selection, and timeline state |
| Map (deferred) | MapLibre GL JS | Incident-context map with approximate camera and supported-location markers |
| Reconstruction (deferred) | React Flow | Clickable 2D statement-based scene with nodes, movement arrows, and alternate interpretations |
| API | Python, FastAPI, Pydantic | Typed HTTP API and validation for incidents, recordings, observations, sitreps, and corrections |
| Persistence | PostgreSQL, SQLAlchemy, Alembic | Durable, queryable, versioned evidence and report state |
| Object storage | Local filesystem for development; S3-compatible storage when hosted | Store uploaded videos, released clips, and source media outside PostgreSQL |
| Media | FFmpeg and native HTML video | Duration/format validation, audio extraction, clip generation, and source playback |
| Background jobs (deferred) | Celery with Redis | Incremental transcription/extraction, retries, progress, and backlog visibility |
| Live updates | HTTP playback polling now; SSE later | Shared replay state now; worker progress and sitrep versions later |
| Runtime | Docker Compose | Local API, PostgreSQL, and frontend services; worker and Redis later |

When the processing milestone begins, keep transcription and language-model providers behind interfaces. The provider is an implementation detail; the durable contract will be the validated observation schema and its source citations. Select models by testing the staged recordings for timestamp quality, extraction accuracy, cost, and processing delay.

## System shape

```text
Browser dashboard (React)
    | HTTP API, playback polling, byte-range video requests
FastAPI API -------- PostgreSQL (incidents, recordings, playback runs)
    |
    +-- FFprobe + FFmpeg: validate incoming media
    +-- Local media volume: validated original recordings
```

The backend owns the simulation cutoff. In the later processing milestone, it will release only eligible segments (initially 10 seconds, configurable). No model may receive the complete recording or media beyond that cutoff. The current media route serves original recordings for synchronized playback; it is not a processing or cutoff-enforcement API and must never be connected directly to a model.

## Core domain rules

These rules are correctness requirements, not optional UI behavior:

1. **Observations are the evidence layer.** A generated sitrep or reconstruction can summarize observations but cannot replace, rewrite, or become evidence of its own.
2. **Every factual report item is cited.** Store one or more observation IDs and source timestamps with each sitrep item; reject or flag uncited output.
3. **Recorded time and claimed event time are separate.** A speaker may describe an earlier event. Unknown times remain unknown.
4. **Camera labels do not identify speakers.** Preserve unknown or ambiguous speakers and never match people across cameras automatically.
5. **Uncertainty is explicit.** Preserve qualifiers, unclear words, source type, conflict, and reviewer status. Do not invent truthfulness or scene-certainty percentages.
6. **Conflicts remain visible.** Keep both claims and their sources. A later state update (for example, “waiting for help” followed by “help arrived”) is different from a contradiction.
7. **Runs are isolated.** Restarting playback creates a new run ID. Results from a prior run cannot enter the new run.
8. **Ordering is deliberate.** Segments can finish out of order, but older results must not overwrite newer status automatically. Serialize or version incident updates.
9. **Corrections are additive.** Preserve the original extraction and record reviewer corrections or exclusions separately. Reports and reconstruction must honor the effective reviewed state.
10. **Lag is honest.** Display current playback time, latest analyzed time, worker status, errors, and backlog. A 10-second release interval is not an end-to-end latency promise.

## Target MVP data model

The current schema contains only incidents, recordings, and playback runs. The remaining records below belong to the later processing milestone. Add fields only when they support a requirement or an observed implementation need.

- **Incident:** ID, title, context, simulation clock, active run ID, and lifecycle status.
- **Recording:** ID, incident ID, camera label, file/object reference, format, duration, start offset, validation status, and processing status.
- **Run:** ID, incident ID, started/restarted time, cutoff, per-feed analyzed times, and status.
- **Segment/job:** recording ID, run ID, local and incident time range, release time, queue state, attempts, error, and completion time.
- **Observation:** ID, run ID, recording/segment references, transcript excerpt, source clip reference, local and incident timestamp ranges, optional claimed event time, text, source type, speaker label, supported subjects/locations, uncertainty, and review state.
- **Scene item:** ID, linked observations, current status, last-reported time, possible conflicts, and versioned history.
- **Sitrep version/item:** cutoff, per-feed analyzed times, overview, latest changes, current status, disputed/unknown items, event history, and linked observation IDs.
- **Review action:** original observation ID, action (`reviewed`, `corrected`, or `excluded`), replacement text/metadata when applicable, reviewer note, and timestamp.

## Dashboard contract

The incident workspace should follow this order:

1. Map-centered main stage with the selected body-camera feed, camera labels/status, playback time, latest analyzed time, and approximate supported location.
2. Statement-based reconstruction immediately after the main stage. It is a schematic interpretation, not measured geometry or a visual-identification system.
3. Persistent shared playback controls and incident clock.
4. Current cited sitrep with latest changes and unresolved information.
5. Expandable event history and evidence panel.

Selecting a marker, feed, reconstruction object, or sitrep item stays within the same incident context and opens linked observations and source clips. Selection and evidence review must not advance the main clock or expose unreleased media.

## Target processing workflow (later milestone)

1. Create an incident and upload one or more short MP4 recordings.
2. Validate media, calculate duration, assign camera labels, and manually set start offsets.
3. Start a run and synchronize replay using the shared incident clock.
4. Release eligible segments per feed; enqueue work and show queue/lag state.
5. Extract audio with FFmpeg, transcribe with timestamps, and validate structured model output.
6. Save source-linked observations, including uncertainty and attribution.
7. Group supported cross-camera references, preserve duplicate-capture caveats, detect conflicts, and version state changes.
8. Generate a cited sitrep from observations released for that cutoff.
9. Derive the reconstruction from the same observations.
10. Allow source review, correction, or exclusion, then regenerate affected views while retaining history.

## Full MVP build order

Implement and verify in this order:

1. Upload, validate, manually align, and play two recordings in the map/live main stage.
2. Release one segment and produce observations with correct source timestamps.
3. Process both recordings incrementally with a durable observation store and visible lag.
4. Generate source-linked sitrep versions with latest changes, history, conflicts, and unknowns.
5. Add evidence playback, review actions, corrections, and exclusions.
6. Add the statement-based reconstruction and verify that it updates from observations without becoming a dependency of the core pipeline.
7. Run the staged end-to-end scenario and measure actual processing delay.

## Definition of done for the MVP

Using two short staged recordings, demonstrate that:

- Camera A creates a cited sitrep item.
- Camera B contributes a useful detail absent from Camera A.
- A later segment changes current status while preserving history.
- A disagreement remains visible with both sources.
- Clicking an item opens the correct source clip.
- Correcting an extraction changes the sitrep without destroying the original source record.
- Future information never appears early, and per-feed lag or failure is visible.

## Local development

The normal stack requires Docker with Docker Compose:

```sh
cp .env.example .env
docker compose up --build
```

Open [the dashboard](http://localhost:5173) or [API documentation](http://localhost:8000/docs). The API applies migrations before starting. PostgreSQL and media use separate named volumes that persist through ordinary restarts and `docker compose down`. The local stack binds published ports to loopback; authentication and production deployment are deferred. Do not use `docker compose down -v` unless you intend to erase both stored metadata and uploaded media.

For development outside Docker, use Node 22.12+ and Python 3.12+. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e 'apps/api[test]'
npm ci
cp .env.example .env
```

Run these in separate terminals:

```sh
npm run dev:db
npm run dev:api
npm run dev
```

`dev:db` runs a project-local PostgreSQL instance and preserves its data in `.data/postgres`. It is an alternative to the Compose database; run only one on port 5432. `dev:api` runs migrations then the API, using the project-local FFmpeg/FFprobe binaries installed with npm. Set `FFMPEG_BINARY` and `FFPROBE_BINARY` to use system binaries instead. Vite forwards `/api` requests to `127.0.0.1:8000` (`API_PROXY_TARGET` overrides this during development). Native and Docker data directories are independent.

Useful commands:

```sh
npm run api:migrate
npm run typecheck
npm run lint
npm run test
npm run test:api
npm run build
npm run test:e2e
```

Browser tests require the running API and web app, plus installed Google Chrome. They create real synthetic MP4 files and disposable incidents labeled `E2E`; they never seed synthetic analysis into the application. They preserve created incidents for inspection. Override `E2E_BASE_URL`, `E2E_API_URL`, and `E2E_BROWSER_CHANNEL` as needed. Tests use the npm-installed FFmpeg binary by default (`FFMPEG_BINARY` overrides it). API unit tests use isolated temporary SQLite databases; set `TEST_DATABASE_URL` to a local test database to run the same suite with PostgreSQL. Each case creates and removes only its own random database schema.

### Verification record

Verified locally on September 11, 2026, with Python 3.12, Node 26, PostgreSQL 18.4, and Google Chrome. Docker and CI use PostgreSQL 17 and Node 22; their execution remains pending.

- Frontend TypeScript checks, ESLint, seven clock/alignment unit tests, and production build pass.
- Foundation API tests pass on SQLite and again on PostgreSQL: real media validation and byte ranges, invalid/truncated/oversized files, the shared validation deadline, persistent state, new runs, concurrent controls, single-camera playback, more than three cameras, upload/playback races, and commit-failure cleanup.
- Four browser scenarios pass: real uploads/alignment/reload/resume/restart; independent feed completion; corrupt upload rejection; lost-clock connection and recovery.
- Dependency audit reports no known npm vulnerabilities for the installed lockfile.
- A process crash between moving a validated file into storage and committing its row can leave an unreferenced file. Normal failure paths remove staged files; orphan recovery and power-loss durability need a later storage-hardening pass.

The GitHub Actions workflow repeats checks against PostgreSQL, validates Compose configuration, and runs the browser suite. It has been added locally, not triggered or published.

Worker commands do not exist yet because the processing workflow is deferred.

Every feature change must update this README's status section and include the relevant verification result. Prefer small, reviewable changes that preserve the domain rules above. Do not add accounts, multi-user collaboration, live camera integrations, automatic clock synchronization, facial recognition, cross-camera person identification, tactical recommendations, automated report filing, calibrated probability scores, production agency integrations, full 3D reconstruction, or continuous visual analysis unless the scope is explicitly changed.

## Live transcription (Grok STT)

Live transcription is enabled per camera feed, driven by the same backend cutoff that owns replay.

- Provider: xAI Grok batch Speech-to-Text (`POST https://api.x.ai/v1/stt`) with speaker diarization enabled, kept behind a small `Transcriber` interface in `apps/api/bop_api/transcription/`.
- Pipeline: playback advances → backend releases eligible segments (default 10 s, `SEGMENT_SECONDS`) → FFmpeg extracts only that window's audio to a temporary mono 16 kHz WAV → Grok returns text, word timestamps, and anonymous speaker ids → the frontend polls `GET /api/incidents/{id}/transcripts` at ~1 Hz and appends completed cues (with Speaker 1 / Speaker 2 turns when multiple voices are detected) under each camera.
- Domain rules honored: no model ever sees media beyond the cutoff, transcripts are keyed by `run_id` so restart isolates them, failed segments never advance the analyzed cutoff, silent recordings are marked `empty` without a provider call, and speaker labels stay anonymous (never auto-mapped to officer/witness or matched across cameras).

Configure the API key locally and do not commit it:

1. Copy `.env.example` to `.env` and set `XAI_API_KEY` to a key created in the [xAI console](https://console.x.ai/).
2. Docker Compose reads `.env` automatically; native `dev:api` inherits the shell environment (put it in `.env` or export it in the shell).
3. Verify with `curl http://127.0.0.1:8000/api/health` — the JSON body's `transcription_configured` becomes `true` when the key is present. The key itself is never returned.

## Questions and current assumptions

The user approved React/TypeScript, FastAPI, PostgreSQL, Docker Compose, real uploads, manual alignment, and synchronized replay for this milestone, with particular emphasis on correctness. Processing is deferred and analysis views stay empty until real results exist. The first model provider, map tiles, authentication boundary, and hosting environment are decisions for later milestones.
