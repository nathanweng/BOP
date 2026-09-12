# Incident knowledge map — next-stage functional specification

## 1. Purpose

Add an Obsidian-style, source-linked knowledge map for a fully processed incident run. The map helps an investigator explore how witnesses, responder statements, evidence, events, locations, subjects, and unresolved questions relate to one another.

The map is a navigable projection of structured observations. It is not an independent source of truth, and it must never turn a generated summary into evidence. Every factual node and relationship must lead back to one or more transcript segments and source-video timestamps.

This stage begins only after every recording in the active run has reached the end and every expected transcript segment has finished successfully. Once the complete graph exists, moving the incident timeline changes the knowledge cutoff shown in the graph. This provides an evolving view without generating conclusions from a partially processed run or revealing later information at an earlier cutoff.

## 2. Feasibility assessment

This feature is feasible with the current React, FastAPI, SQLAlchemy, and PostgreSQL architecture.

Useful foundations already exist:

- recordings, playback, transcripts, and event history are isolated by `run_id`;
- transcript segments retain recording-local and incident-clock ranges;
- event-history entries link to source segments and can be marked current, outdated, or disproven;
- the browser already has a shared incident clock, selected-camera state, and source-video review;
- restart creates a new run, preventing old analysis from entering a new result.

The current event-history record is not rich enough to power the map by itself. It stores a concise event title with one primary source and an optional later status source. It does not model distinct claims, people, evidence items, locations, multi-source support, uncertain identity, or typed relationships. The next stage therefore needs a structured observation and entity layer before graph rendering.

The highest-risk areas are model extraction quality, accidental identity merging, incorrect conflict classification, graph clutter, and defining completion reliably. These are manageable with strict schemas, source validation, conservative entity resolution, deterministic readiness checks, versioned projections, and human review.

## 3. Product decisions

### 3.1 Separate workspace tab

Add a top-level incident tab named **Connections** beside the existing **Timeline** workspace. Keep upload, synchronized replay, transcripts, and the event-history table in Timeline. Connections contains the graph, graph filters, temporal controls, and a source-evidence inspector.

This separation is recommended because the graph needs most of the available canvas and serves a different task: investigating relationships rather than watching footage. Both tabs remain in the same incident and active `run_id`. Opening a source from Connections uses an independent evidence player and does not change the shared incident clock.

### 3.2 Complete first, reveal by cutoff afterward

Graph extraction does not run incrementally during replay. The backend first processes the entire active run and builds a complete, validated graph. Afterward, the graph endpoint accepts a `cutoff_seconds` value and returns only the knowledge available by that time.

This reconciles the two requirements:

- generation is permitted only when all clips have finished transcription and processing;
- after generation, timeline movement still makes the graph evolve as later claims, clarifications, and contradictions become known.

The system may use later content to create stable internal entity IDs, but an earlier cutoff must not reveal a later label, relationship, status, source count, or clarification. If doing so cannot be guaranteed, keep entities separate at the earlier cutoff.

## 4. Terminology and truth model

- **Source segment:** a completed transcript segment with recording and timestamp provenance.
- **Observation:** one atomic, source-linked assertion extracted from a source segment, preserving attribution and uncertainty.
- **Entity:** a referenced person, role, place, object, recording, evidence item, or unresolved identity. An entity is a label for navigation, not proof that two references identify the same real-world subject.
- **Event:** an occurrence or reported occurrence with recorded time and, when explicitly stated, a separate claimed event time.
- **Relationship:** a typed link between two graph items, supported by observations.
- **Knowledge time:** the incident-clock time when information became available in a recording.
- **Claimed event time:** when a speaker says an event occurred. Unknown stays unknown.
- **Graph projection:** the nodes, edges, and statuses visible at a selected knowledge cutoff.

Generated status vocabulary:

- `active`: no later qualifying update is known at this cutoff;
- `superseded`: later information updates or replaces the earlier state without necessarily conflicting with it;
- `disputed`: supported sources make incompatible claims and neither is selected as authoritative;
- `contradicted`: a later source explicitly conflicts with the claim;
- `excluded`: a reviewer has excluded the underlying observation.

`disproven` is reserved for a reviewer decision or a configured authoritative-evidence rule. A language model must not decide that a witness is lying or that one conflicting account is true. The current event-history label `disproven` should be migrated or presented as `contradicted` unless it has reviewer-backed provenance.

## 5. Readiness gate

### 5.1 Backend-owned readiness

Expose a server-computed processing state for the active run. The frontend must not infer readiness solely from the playback state or its processed-through progress bar.

Suggested states:

- `awaiting_replay`: the incident has not reached its full duration;
- `transcribing`: one or more expected windows are queued or processing;
- `blocked`: a window is failed or missing, or a required provider is not configured;
- `ready_for_graph`: all expected windows are terminal and successful, including silent `empty` windows;
- `building_graph`: graph extraction or validation is running;
- `graph_ready`: a complete graph version is available for the active run;
- `graph_failed`: the build failed while any previous valid version remains available.

### 5.2 Exact completion rule

A run is `ready_for_graph` only when all of the following are true:

1. At least one validated recording belongs to the incident.
2. The active playback run has reached the incident duration and is in `ended` state.
3. The backend calculates every expected segment window from each recording's duration, offset, and configured segment size.
4. Exactly one transcript row exists for every expected window in the active `run_id`.
5. Every expected row is `completed` or `empty`; none is `queued`, `processing`, `failed`, or missing.
6. Required transcript and graph-analysis providers are configured.

A failed segment blocks graph creation and presents the failed camera/window plus a retry action. Silence does not block completion. Seeking directly to the end may release all windows under the existing playback rules, but graph readiness still waits for every window to finish.

### 5.3 Build behavior

- Offer **Build connections map** when the run becomes `ready_for_graph`; automatic build may be added later.
- Capture the active `run_id`, graph input revision, segment IDs, and prompt/schema version before the provider call.
- Revalidate them before commit. Discard the result if the incident restarted, alignment changed, or inputs no longer match.
- Make builds idempotent for the same input hash.
- Keep the last valid graph visible if a rebuild fails, clearly labeled with its version and stale reason.

## 6. Graph contents

### 6.1 Node types

The first version supports:

| Node | Meaning | Minimum display |
| --- | --- | --- |
| Claim | One atomic attributed assertion | short text, speaker/source type, knowledge time, status |
| Event | A reported occurrence or state change | label, time basis, status, source count |
| Person/role | Named person or conservative role reference | source-given label; identity uncertainty |
| Witness | A speaker explicitly identified as a witness | supplied/anonymous label; never inferred from camera label |
| Responder | A speaker explicitly identified as a responder | supplied/anonymous label |
| Location | A supported named or relative place | label and uncertainty |
| Evidence | A referenced physical, digital, documentary, or media item | type, description, custody/status if stated |
| Object/vehicle | A relevant non-evidence item | supported label |
| Question | An unresolved gap or ambiguity | question and last-updated time |
| Recording | A source camera | camera label and transcript coverage |

Prefer claim nodes as the hub between a source recording and a person, event, evidence item, or location. This keeps provenance visible and prevents an attractive edge from concealing the statement that created it.

### 6.2 Edge types

Initial typed relationships:

- `stated_by`, `captured_by`, `supports`, and `mentions`;
- `occurred_at`, `before`, `after`, and `updates`;
- `involves`, `located_at`, `moved_to`, and `associated_with`;
- `corroborates`, `duplicates_capture_of`, `disputes`, and `contradicts`;
- `answers` and `raises_question`.

Every relationship stores its supporting observation IDs. Cross-camera capture of the same utterance must use `duplicates_capture_of` when independence is unknown, not `corroborates`.

### 6.3 Identity resolution

- Never infer a speaker's identity from a camera label.
- Never merge anonymous speakers across recordings automatically.
- Resolve entities only from explicit naming or a high-precision shared reference supported in text.
- Store uncertain matches as candidate links with `possible_same_as`; do not collapse nodes.
- Allow a reviewer to merge or split entities later while preserving the original references and audit history.

## 7. Temporal behavior

The Connections tab has two time modes:

- **Final view:** shows all knowledge from the fully processed run.
- **At timeline:** follows the shared incident clock as a knowledge cutoff.

At cutoff `T`:

- include only observations whose source segment became available at or before `T`;
- include an entity or relationship only if supported by an included observation;
- calculate status using only updates with knowledge time at or before `T`;
- do not reveal later contradiction badges, revised names, support counts, or tooltips;
- show claimed event time separately and never use it to bypass the knowledge cutoff.

Example: at 00:30, a claim that a person was at the front entrance is active. A clarification recorded at 01:10 says it was the rear entrance. Before 01:10, the first claim appears active. At and after 01:10, the first claim becomes superseded and the rear-entrance relationship appears. Returning to 00:30 restores the earlier state without erasing history.

All temporal projection rules should be deterministic application code operating on persisted versions. Timeline scrubbing must not call a model.

## 8. User experience

### 8.1 Locked state

Before `graph_ready`, Connections shows:

- current readiness state and overall processed coverage;
- per-camera expected, completed, empty, failed, queued, and processing segment counts;
- a plain explanation that the map is intentionally unavailable until all clips finish;
- retry controls for failed windows;
- the Build action only when eligible.

Do not render a partial graph as if it were the incident map.

### 8.2 Graph canvas

Use a React Flow-style pannable canvas with zoom, fit-to-selection, minimap, keyboard navigation, and deterministic initial layout. Do not make free dragging alter factual relationships; optionally persist layout positions separately as presentation preferences.

Default visual language:

- node shape or icon identifies type, not truth status;
- neutral styling for active assertions;
- amber border/badge for superseded updates;
- red split border and explicit label for disputed or contradicted claims;
- dashed styling for uncertainty or possible identity;
- muted/struck styling for reviewer-excluded observations;
- a distinct reviewer badge for reviewer-confirmed disproven claims.

Never rely on color alone. Labels, patterns, and accessible names must communicate the same state.

### 8.3 Filters and focus

Provide filters for node type, camera/source, speaker, status, and time. Include search, one-hop/two-hop focus, hide isolated nodes, and a “show only conflicts” toggle. Default to a curated event-and-claim view rather than showing every node at once. Expand related people, evidence, and recordings on demand.

### 8.4 Inspector and evidence playback

Selecting a node or edge opens an inspector containing:

- its plain-language label and type;
- status and the reason for that status;
- uncertainty and attribution exactly as supported;
- all supporting and conflicting observations;
- camera label, transcript excerpt, and incident/local timestamps;
- **Open source** controls for the independent evidence player;
- history of later updates and reviewer actions.

Opening evidence must not seek, pause, or advance the shared replay clock. The inspector should make it clear whether the user is viewing a source, a generated claim, or a reviewer decision.

## 9. Data model

Names are illustrative; migrations should use project conventions.

### `observations`

- `id`, `run_id`, `recording_id`, `segment_id`;
- local and incident timestamp ranges;
- optional claimed event time/range and its precision;
- atomic text, transcript excerpt, source type, anonymous speaker label;
- qualifiers/uncertainty as structured values plus original wording;
- extraction schema/prompt version and created time.

### `entities`

- `id`, `run_id`, `type`, canonical display label;
- first-known incident time;
- identity state (`explicit`, `anonymous`, `possible_match`, `reviewer_resolved`);
- creation source and version metadata.

### `entity_mentions`

- `entity_id`, `observation_id`, source wording, role in claim;
- optional location or temporal qualifier.

### `graph_relations`

- `id`, `run_id`, typed source and target references;
- supporting observation IDs through a join table;
- knowledge-valid interval (`known_from`, optional `superseded_at`);
- generated status, uncertainty label, and extraction version.

### `claim_updates`

- original claim/relationship ID;
- later observation ID;
- update type (`supersedes`, `disputes`, `contradicts`);
- knowledge time and rationale constrained to source text.

### `review_actions`

- target observation/entity/relation;
- action (`confirm`, `correct`, `exclude`, `mark_disproven`, `merge`, `split`);
- replacement fields or note, actor, and timestamp;
- immutable audit record.

### `graph_versions`

- `id`, `run_id`, full-cutoff seconds, input hash;
- status, schema/prompt/model version, created/completed times, error summary;
- counts and validation results;
- never store graph layout as evidence.

Prefer normalized source joins over opaque graph JSON. A cached projection JSON may be stored for performance if it is reproducible from normalized records.

## 10. API contract

Suggested endpoints:

- `GET /api/incidents/{incident_id}/processing-status`
- `POST /api/incidents/{incident_id}/graph-builds`
- `GET /api/incidents/{incident_id}/graph-builds/current`
- `GET /api/incidents/{incident_id}/graph?cutoff_seconds={T}`
- `GET /api/incidents/{incident_id}/graph/items/{item_id}`
- `POST /api/incidents/{incident_id}/review-actions`
- `POST /api/incidents/{incident_id}/transcript-segments/{segment_id}/retry`

Graph responses include `run_id`, graph version, requested/effective cutoff, readiness, nodes, edges, legends, and source-count metadata. Reject a cutoff beyond the completed run and never return data from a non-active run by default.

The build endpoint returns `409` with structured blockers when the run is incomplete, and `202` when a build starts. For the local prototype an in-process worker is acceptable, but build state must be durable; a later Celery/Redis migration must not change the API contract.

## 11. Extraction and validation pipeline

1. Compute and persist the readiness manifest for the active run.
2. Convert completed transcript segments into atomic observations using a strict provider-neutral schema.
3. Reject observations with unknown segment IDs, out-of-range timestamps, or missing source citations.
4. Extract entity mentions without cross-camera identity assumptions.
5. Link observations into events and typed relations using conservative, source-backed rules.
6. Detect updates and conflicts; preserve every original claim.
7. Validate all node and edge references, temporal ordering, status provenance, and run isolation.
8. Commit one graph version transactionally if the active run and input hash are unchanged.
9. Produce cutoff projections deterministically from stored knowledge times.

Chunk large incidents by transcript segment ranges, then run a validation/reconciliation pass. Never silently truncate at the current event-history limits of 120,000 characters or 500 events. Surface configured limits before build and record any explicit omission as a build failure, not a complete graph.

## 12. Failure, correction, and rebuild rules

- Provider or validation failure leaves the run in `graph_failed` and retains the prior valid graph version.
- Retrying a failed transcript segment invalidates graph readiness until the segment succeeds.
- Restarting creates a blank graph state for the new run; earlier run graphs remain stored but are not mixed into the active view.
- Reviewer corrections are additive and take effect in new projections without deleting original observations.
- Corrections that change extracted structure create a new graph version or deterministic overlay and retain the prior version for audit.
- A graph build never rewrites immutable event-history timestamps or source transcript text.

## 13. Privacy and safety constraints

- Do not add facial recognition, appearance-based identity, or automatic cross-camera person matching.
- Do not infer intent, guilt, witness reliability, protected traits, or tactical recommendations.
- Do not expose model confidence percentages as truth scores.
- Escape all model-produced labels and treat transcript content as data, never instructions.
- Keep provider credentials server-side and avoid logging transcript/provider response bodies.
- Apply the same authorization boundary to graph data, transcripts, and video when authentication is introduced.

## 14. Delivery plan

### Phase A — completion contract

- backend readiness manifest and endpoint;
- exact expected-window coverage calculation;
- failed-window retry;
- frontend locked/progress state;
- tests for offsets, trailing partial windows, silence, failures, restarts, and seek-to-end.

### Phase B — observations and graph persistence

- migrations for observations, entities, mentions, relations, updates, and versions;
- provider-neutral extraction interface and strict validation;
- input hashing, durable build jobs, and run-change commit guard;
- chunking for bounded provider requests.

### Phase C — Connections tab

- new Timeline/Connections navigation;
- graph canvas, legend, filters, search, and inspector;
- source-video playback isolated from the shared clock;
- loading, empty, failed, and stale states.

### Phase D — temporal projection and review

- Final view and At timeline mode;
- deterministic cutoff projection;
- visual update/conflict history;
- reviewer correction, exclusion, disproven, merge, and split actions.

### Phase E — verification and hardening

- staged multi-camera acceptance fixture;
- accessibility and large-graph performance tests;
- live provider quality evaluation;
- audit of source coverage, temporal leakage, and restart isolation.

## 15. Acceptance criteria

Using a fully processed staged incident with at least two cameras:

1. Connections remains locked until the run ended and every expected segment is `completed` or `empty`.
2. One failed or missing segment blocks the build and is identified with a retry action.
3. Building creates a run-scoped graph in which every factual node and edge has at least one valid source observation.
4. A witness claim, responder claim, evidence item, event, and location can be explored and opened at the correct source-video time.
5. The same utterance captured by two cameras is not presented as independent corroboration.
6. Anonymous speakers from different cameras are not merged automatically.
7. At an earlier cutoff, no later label, node, edge, support count, or status is visible.
8. A later clarification marks an earlier claim superseded while preserving it; scrubbing backward restores its earlier active state.
9. Incompatible accounts remain visible together and are labeled disputed or contradicted without the system choosing a winner.
10. Only a reviewer or configured authoritative rule can mark a claim disproven, and the action is auditable.
11. Opening graph evidence does not change the shared incident clock.
12. Restarting produces a locked, empty graph state for the new run and never leaks items from the previous run.
13. A failed rebuild preserves the last valid graph and clearly reports that it is stale.
14. Keyboard navigation, non-color status cues, and readable focus states work across the graph and inspector.

## 16. Deferred additions

After the first accepted version, consider:

- saved investigator views and pinned paths through the graph;
- side-by-side comparison of two cutoff times or two competing interpretations;
- unresolved-question suggestions tied to missing graph relationships;
- spatial reconstruction integration, with the knowledge map remaining the provenance layer;
- export of a source appendix and graph snapshot with version/cutoff metadata;
- collaboration, comments, and reviewer roles after authentication exists;
- aggregate graph metrics for navigation only, never as guilt or reliability scoring.

Full 3D reconstruction, automated visual evidence extraction, facial recognition, automatic person identification, and tactical recommendations remain out of scope.
