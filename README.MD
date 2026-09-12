# Bodycam Situation Report

This repository is the working specification and implementation home for a web application that turns synchronized, prerecorded body-camera recordings into a continuously updated, source-linked situation report (sitrep).

The product is an incident-review workspace for supervisors or arriving responders. It must make clear what is known, what changed, what is disputed, and what remains unknown. Audio speech is the primary analysis input. Video is retained for playback and evidence review; continuous visual analysis is outside the MVP.

The authoritative product requirements are in [Bodycam MVP Spec.md](./Bodycam%20MVP%20Spec.md). Read this README and the specification before changing the architecture or adding a feature.

## Project status

The repository currently contains the product specification and project rules. Application code, infrastructure, and automated tests have not been created yet.

As implementation begins, keep this section current:

- **Complete:** verified functionality available to other agents.
- **In progress:** work that is underway or partially integrated.
- **Needs verification:** implemented work that still needs an end-to-end or visual check.
- **Deferred:** explicitly outside the MVP or reserved for a later stage.

## Recommended stack

Use a small monorepo with a browser client, an API, and a media-analysis worker:

| Area | Choice | Purpose |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite | Interactive incident dashboard without unnecessary server-rendering complexity |
| UI | Tailwind CSS and shadcn/ui | Operations-center layout, panels, status labels, and accessible controls |
| Client state | TanStack Query and Zustand | Server data/cache plus local playback, selection, and timeline state |
| Map | MapLibre GL JS | Incident-context map with approximate camera and supported-location markers |
| Reconstruction | React Flow | Clickable 2D statement-based scene with nodes, movement arrows, and alternate interpretations |
| API | Python, FastAPI, Pydantic | Typed HTTP API and validation for incidents, recordings, observations, sitreps, and corrections |
| Persistence | PostgreSQL, SQLAlchemy, Alembic | Durable, queryable, versioned evidence and report state |
| Object storage | Local filesystem for development; S3-compatible storage when hosted | Store uploaded videos, released clips, and source media outside PostgreSQL |
| Media | FFmpeg and native HTML video | Duration/format validation, audio extraction, clip generation, and source playback |
| Background jobs | Celery with Redis | Incremental transcription/extraction, retries, progress, and backlog visibility |
| Live updates | Server-Sent Events (SSE) | Push job progress and new sitrep versions to the dashboard |
| Runtime | Docker Compose | Reproducible local API, worker, Redis, PostgreSQL, and frontend services |

Keep transcription and language-model providers behind interfaces. The provider is an implementation detail; the durable contract is the validated observation schema and its source citations. Select models by testing the staged recordings for timestamp quality, extraction accuracy, cost, and processing delay.

## System shape

```text
Browser dashboard
    | REST + SSE
FastAPI API  -------- PostgreSQL (incidents, observations, sitrep versions, corrections)
    |                         |
    | dispatches jobs         | source metadata and run state
Redis queue  ----------- Celery media worker
    |
FFmpeg -> timestamped audio segment -> transcription -> structured observation extraction
    |
cross-camera grouping -> status/history/conflict handling -> cited sitrep + reconstruction state
```

The backend owns the simulation cutoff. When the incident clock advances, it releases only the eligible 10-second media segments (configurable). A model must never receive the complete recording or any media beyond the current cutoff. The browser may inspect an already released source clip without advancing the main clock.

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

## Minimum data model

The initial schema should cover these records. Add fields only when they support a requirement or an observed implementation need.

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

## Processing workflow

1. Create an incident and upload two or three short MP4 recordings.
2. Validate media, calculate duration, assign camera labels, and manually set start offsets.
3. Start a run and synchronize replay using the shared incident clock.
4. Release eligible segments per feed; enqueue work and show queue/lag state.
5. Extract audio with FFmpeg, transcribe with timestamps, and validate structured model output.
6. Save source-linked observations, including uncertainty and attribution.
7. Group supported cross-camera references, preserve duplicate-capture caveats, detect conflicts, and version state changes.
8. Generate a cited sitrep from observations released for that cutoff.
9. Derive the reconstruction from the same observations.
10. Allow source review, correction, or exclusion, then regenerate affected views while retaining history.

## Build order

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

## Local development expectations

Once the application scaffold exists, provide one documented command to start the full local stack with Docker Compose and separate commands for frontend, API, worker, migrations, linting, and tests. New agents should be able to clone the repository, copy an example environment file, start dependencies, and run the verification suite without manually editing production code.

Every feature change must update this README's status section and include the relevant verification result. Prefer small, reviewable changes that preserve the domain rules above. Do not add accounts, multi-user collaboration, live camera integrations, automatic clock synchronization, facial recognition, cross-camera person identification, tactical recommendations, automated report filing, calibrated probability scores, production agency integrations, full 3D reconstruction, or continuous visual analysis unless the scope is explicitly changed.

## Questions and current assumptions

There are no blocking questions for scaffolding the project. This README assumes a single-incident MVP, prerecorded MP4 input, manual synchronization, local development through Docker Compose, and a replaceable transcription/LLM provider. Decisions that need to be made before implementation are the first model provider, map tile provider, authentication boundary if deployment is required, and the target hosting environment; none changes the core domain model.
