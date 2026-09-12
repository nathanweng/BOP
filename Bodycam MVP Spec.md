# Body-camera situation report — MVP functional specification

## Objective
Build a web app that combines uploaded recordings of the same incident into a continuously updated, source-linked situation report (sitrep). A supervisor or arriving responder should quickly understand what is known, what changed, and what remains uncertain across camera perspectives.

The primary inputs are witness and responder speech captured in body-camera audio. Video remains available for playback and human source review; automated visual extraction is not required for the MVP. This prioritizes a smaller processing workload for a future edge deployment, without claiming that the prototype already runs on-device.

The MVP uses prerecorded footage replayed as synchronized feeds to simulate an unfolding incident. Direct camera streaming is outside this build. The system may access only media up to the current simulation time when generating observations and summaries.

## Current implementation milestone: upload and replay foundation

Implement incident creation, durable MP4 storage and validation, manual start offsets, and synchronized replay with a backend-owned incident clock. Prioritize reliability and functional structure over visual polish. The main stage reserves the map area and plays actual uploaded body-camera recordings; the reconstruction area follows it.

Processing steps in the full MVP specification below are future requirements. Do not implement segment scheduling, transcription, observation extraction, grouping, sitrep generation, reconstruction generation, or their placeholder services/interfaces in this milestone. Map/location, reconstruction, sitrep, history, and evidence areas use honest empty states until their supported data exists. No fabricated incident facts or mock analysis results.

Foundation acceptance: two valid recordings can be uploaded, saved, aligned, replayed, paused, resumed, and restarted. Reload restores the saved incident, alignment, and backend playback state. Invalid files and storage failures produce actionable errors. Delayed feeds remain stopped before their offsets, completed feeds are labeled, stale playback commands cannot override newer state, and restart creates a new run at zero. Verify this with real media and PostgreSQL in addition to isolated tests.

## 1. Create an incident and upload recordings
- Create one incident workspace with a title and optional user-supplied context.
- Upload one or more short body-camera videos of the same incident; support one declared format such as MP4 for the MVP.
- Give each recording a camera label, such as Camera A or Officer B. A camera label does not identify every person speaking in its audio.
- Display upload/validation status, video duration, and actionable errors for unsupported or unreadable files.
- Allow a manual start-time offset for each recording against the incident clock. Recordings may start at different times; automatic synchronization is deferred.

Acceptance: two recordings can be uploaded, labeled, aligned, and opened in one incident workspace.

## 2. Synchronized replay
- Provide a shared incident clock with play, pause, and restart controls.
- Play each recording at the appropriate local video time using its configured offset. Show when a feed has not started or has ended.
- Release short media segments to the analysis pipeline as simulation time advances. Start with a configurable 10-second segment size; this is a design target, not a latency guarantee.
- Extract and transcribe audio within each released segment, then analyze the transcript. Keep video for source playback; continuous frame analysis is outside the core pipeline. Do not send the complete recording to an analysis model.
- Display both current playback time and the latest analyzed time for each feed so processing lag is visible.
- Pause stops release of new segments; already released work may finish. Restart begins a fresh analysis state and excludes responses from the previous run.

Acceptance: information appearing later in a video does not appear in the sitrep before that portion is released.

## 3. Extract source-linked observations
Extract relevant information from each segment into structured records:
- What a witness or responder said, preserving qualifiers and attribution.
- Which camera and local timestamp range support it.
- The corresponding incident timestamp range.
- Evidence type: witness statement, responder statement, or unidentified-speaker statement.
- Speaker label when distinguishable, otherwise unknown; do not assume matching speakers across cameras.
- Relevant location and subjects, only when supported by the source.
- Any explicit uncertainty, such as “I think,” and any unclear audio or ambiguous wording.
- Claimed event time, if stated, separate from the recording time. Unknown event times remain unknown.

Preserve a transcript excerpt and timestamped source clip for each spoken claim. Extract concrete scene information such as assistance status, reported events, access conditions, and unanswered questions. Do not infer intent or assign identities from appearance.

Acceptance: each observation opens supporting media; unclear words, locations, identities, and event times are not silently filled in.

## 4. Combine information across cameras
- Group observations that appear to concern the same event, subject, or location when the evidence supports that association.
- Retain all source references when grouping. If the association is uncertain, keep observations separate or flag the possible link for review.
- Avoid counting one utterance captured by multiple microphones as independent corroboration; use “captured in two recordings” when independence is unknown.
- Detect potential contradictions and display both claims with their sources, without selecting a winner automatically.
- Distinguish a contradiction from a state change: “waiting for help” followed by “help arrived” can be an update.
- Keep prior observations in history when a current status changes.
- Accept segments arriving out of order without letting older observations overwrite newer status automatically.

Acceptance: the demo includes a combined observation from multiple perspectives, a visible state update, and a preserved disagreement or uncertainty.

## 5. Generate and update the shared sitrep
Generate the sitrep from the structured observation records released so far. Each update should contain:
- Current overview: three to five short bullets summarizing the scene.
- Latest changes: new information since the previous update.
- Current status: relevant subjects, locations, assistance, and access conditions, with last-reported times.
- Disputed or unknown information: conflicting claims and unresolved gaps.
- Event history: observations and changes in chronological order, clearly distinguishing recorded time from reported event time.

Every factual sitrep item must link to one or more source observations. Use explicit wording such as “Witness reports…” or “Responder on Camera B reports…” for unverified information. Preserve the previous sitrep while an update is processing or fails. Show an empty state when there is insufficient evidence.

Acceptance: new information from either camera changes the shared sitrep without requiring the user to regenerate the entire incident manually.

## 6. Evidence review and correction
- Clicking a sitrep item shows its supporting observations and opens the relevant camera clip at the cited timestamp.
- Source inspection does not advance the main incident clock or expose future footage to analysis.
- Allow the reviewer to mark an observation as reviewed, correct its text, or exclude an incorrect extraction.
- Preserve the original extraction and record the correction separately. Subsequent sitrep updates must honor reviewer corrections and exclusions.
- Use separate labels for source type, uncertainty/conflict, and reviewer status. Do not show a fabricated percentage for witness truthfulness or overall scene certainty.

Acceptance: a reviewer can trace a claim to its source, correct a mistake, and see the correction reflected in the next sitrep.

## Minimum interface
Use an operations-center layout inspired by Axon Fusus: make the incident itself visible first, keep live source context continuously available, and place analysis and reconstruction in a clearly ordered investigative flow.

One incident dashboard containing:
1. **Main stage — live incident map and body-camera view.** The default stage is a map-centered operational view with the active body-camera feed available alongside or over the map. Show camera/source labels, camera status, current playback time, latest analyzed time, and the selected feed's approximate supported location when known. The map is an incident-context view, not a claim of precise GPS or live production integration; unknown locations remain unknown.
2. **Next — statement-based reconstruction scene.** Directly beneath or immediately after the main stage, show the evolving reconstruction for the current simulation cutoff. It follows the live/map view so responders can first inspect the source context, then review a synthesized interpretation of people, locations, movement, and event order.
3. Shared playback controls and incident clock, persistent while reviewing the main stage or reconstruction.
4. Current sitrep with latest changes and unresolved information.
5. Filterable event history with source details shown in context rather than a separate evidence panel.

Selecting a map marker, body-camera feed, reconstruction object, or sitrep item should keep the dashboard in the same incident context and reveal the linked source clips and observations. Do not let a reconstruction selection advance the incident clock or reveal future footage.

Upload and alignment can be a simple setup screen. Accounts, multiuser collaboration, and complex navigation are not required.

## Minimum information to retain
| Record | Required contents |
| --- | --- |
| Recording | ID, camera label, file reference, duration, start offset, processing status |
| Observation | ID, source clip references and transcript excerpts, recorded time, optional claimed event time, text, source type, uncertainty, reviewer corrections |
| Scene item | ID, linked observations, current status, last-reported time, potential conflicts, history |
| Sitrep version | Simulation cutoff time, per-feed analyzed times, summary items linked to observations, changes since prior version |

Keep observations as the underlying record so a generated summary cannot erase details or become its own unsupported evidence.

## Processing flow
Current foundation: uploaded files → validated durable recordings → manual alignment → shared incident replay → map/body-camera main stage.

Later processing: eligible released segments → per-camera transcription and statement analysis → structured observations → cross-camera grouping and status updates → cited sitrep and statement-based reconstruction. Both analysis views consume the same observations; neither is an input to media playback.

## Definition of done
Using two short recordings of a staged incident, demonstrate:
1. A statement from Camera A creates a cited sitrep item.
2. A statement recorded by Camera B contributes a useful detail absent from Camera A.
3. A later segment changes a current status while preserving its history.
4. A disagreement remains visible with both sources.
5. Clicking an item opens the correct source clip.
6. Correcting an extraction changes the sitrep without destroying the source record.
7. No future information appears early, and per-feed lag or failure is visible.

Measure actual processing delay on the demo files. A 10-second release interval does not imply a 10-second end-to-end delay. If analysis falls behind, display the backlog rather than implying the sitrep is current.

## Build order
1. Upload and manually align two playable recordings, then display them in the map/live body-camera main stage.
2. Extract observations from one released segment with correct source timestamps.
3. Process both recordings incrementally and maintain an observation store.
4. Produce a source-linked sitrep with changes and conflicts.
5. Connect dashboard evidence playback and reviewer corrections.
6. Add the statement-based reconstruction scene immediately after the main stage, then verify the staged end-to-end scenario and measure latency.

## Next-stage capability: evolving scene reconstruction
Priority: implement this immediately after the map/live body-camera main stage and core upload, replay, statement extraction, multi-source sitrep, and source-review workflow function end to end. It is the next view in the incident dashboard, but does not block the reliability of the source-linked MVP workflow.

- Add a lightweight 2D schematic reconstructed from witness and responder statements: labeled people or objects, approximate places, and supported movement arrows. Use simple shapes or icons rather than generated photorealistic scenes or full 3D geometry.
- Label the view “Statement-based reconstruction.” Only render relationships supported by available statements. Leave unspecified positions unknown; a schematic layout is not measured geometry.
- Connect the view to the timeline. As new statements become available, update depicted locations, actions, event order, and uncertainty. Distinguish a change in the scene from a correction to the interpretation of an earlier event.
- Maintain both reported event time and the time clarification became available. Historical playback shows what was understood at that cutoff, without leaking later clarification. Retain revised interpretations of earlier events as versioned updates.
- Represent uncertainty using explicit labels and dashed outlines or ranges. Distinguish witness-stated confidence, source agreement, and reviewer confirmation. Change these indicators when new supporting or conflicting statements arrive; do not invent numerical probabilities.
- Preserve competing interpretations where witnesses disagree, using alternate positions or selectable versions rather than merging them into a single apparently certain scene.
- Clicking an object, movement, or event reveals its supporting statements and source clips. Reviewer corrections update the schematic as well as the sitrep.
- Derive reconstruction state from existing structured observations, with optional relative spatial relationships and event links added later. Do not make the main pipeline depend on reconstruction fields or visual models.

Next-stage acceptance: a witness initially reports that a person was near an entrance; a later clarification identifies a different entrance. The schematic updates the location and support indicators, preserves the original claim, and shows the earlier interpretation when replay is returned to the earlier cutoff.

## Deferred functionality
Live camera integrations, automatic clock synchronization, full 3D reconstruction, continuous automated visual analysis, facial recognition, automatic cross-camera person identification, tactical recommendations, automated report filing, calibrated probability scores, and production agency integrations.
