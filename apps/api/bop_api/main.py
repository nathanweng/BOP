from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException

from .config import Settings
from .database import make_database
from .media import media_path, stage_upload
from .models import (
    EventHistory,
    Incident,
    KnowledgeItem,
    KnowledgeObservation,
    KnowledgeSupport,
    KnowledgeVersion,
    PlaybackRun,
    Recording,
    SituationReportVersion,
    TranscriptSegment,
)
from .event_service import EventService
from .knowledge import KnowledgeService
from .situation import SituationService
from .playback import playback_view, utc
from .schemas import (
    IncidentCreate,
    IncidentSummary,
    IncidentView,
    PlaybackCommand,
    PlaybackView,
    RecordingTranscript,
    RecordingUpdate,
    RecordingView,
    TranscriptSegmentView,
    TranscriptTurnView,
    TranscriptsView,
)
from .transcription import (
    GrokTranscriber,
    NullTranscriber,
    Transcriber,
    TranscriptionService,
    group_speaker_turns,
)

logger = logging.getLogger(__name__)


def transcript_turns_from_words(words_json: str | None) -> list[TranscriptTurnView]:
    """Rebuild anonymous speaker turns from stored word timestamps."""
    if not words_json:
        return []
    try:
        words = json.loads(words_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(words, list):
        return []
    if not any(isinstance(word, dict) and "speaker" in word for word in words):
        return []
    turns = group_speaker_turns(words)
    # Skip a lone unlabeled stream — UI shows plain text instead.
    if len(turns) <= 1 and all(turn.speaker == 0 for turn in turns):
        return []
    return [
        TranscriptTurnView(
            speaker=turn.speaker + 1,
            label=f"Speaker {turn.speaker + 1}",
            text=turn.text,
            local_start_seconds=turn.start,
            local_end_seconds=turn.end,
        )
        for turn in turns
        if turn.text
    ]


class UploadBodyLimit:
    """Reject oversized multipart bodies before their temporary files fill disk."""

    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        maximum = self.limit if scope.get("method") == "POST" and scope["path"].endswith("/recordings") else 65_536
        headers = dict(scope.get("headers", []))
        try:
            size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        if size < 0:
            return await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
        if size > maximum:
            return await JSONResponse({"detail": "The request exceeds the upload size limit."}, status_code=413)(scope, receive, send)
        received = 0
        rejected = False
        response_replaced = False

        async def bounded_receive():
            nonlocal received, rejected
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                rejected = True
                # Starlette closes partially spooled uploads for this exception.
                raise MultiPartException("The request exceeds the upload size limit.")
            return message

        async def bounded_send(message):
            nonlocal response_replaced
            if rejected:
                if not response_replaced:
                    response_replaced = True
                    await JSONResponse({"detail": "The request exceeds the upload size limit."}, status_code=413)(scope, receive, send)
                return
            await send(message)

        await self.app(scope, bounded_receive, bounded_send)


def create_app(
    settings: Settings | None = None,
    clock=None,
    *,
    transcriber: Transcriber | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    engine, sessions = make_database(settings.database_url)
    resolved_transcriber: Transcriber
    if transcriber is not None:
        resolved_transcriber = transcriber
    elif settings.xai_api_key:
        resolved_transcriber = GrokTranscriber(
            settings.xai_api_key,
            url=settings.xai_stt_url,
            language=settings.xai_stt_language,
            timeout_seconds=settings.xai_stt_timeout_seconds,
        )
    else:
        resolved_transcriber = NullTranscriber()
    resolved_clock = clock or (lambda: datetime.now(UTC))
    transcription = TranscriptionService(
        settings,
        sessions,
        resolved_transcriber,
        clock=lambda: utc(resolved_clock()),
    )
    events_service = EventService(settings, sessions, lambda: utc(resolved_clock()))
    knowledge_service = KnowledgeService(settings, sessions, lambda: utc(resolved_clock()))
    situation_service = SituationService(settings, sessions, lambda: utc(resolved_clock()))

    @asynccontextmanager
    async def lifespan(_app):
        settings.media_root.mkdir(parents=True, exist_ok=True)
        transcription.start()
        events_service.start()
        knowledge_service.start()
        situation_service.start()
        try:
            yield
        finally:
            transcription.stop()
            events_service.stop()
            knowledge_service.stop()
            situation_service.stop()
            engine.dispose()

    app = FastAPI(title="BOP (Bizzy Ops) incident workspace", version="0.1.0", lifespan=lifespan)
    app.add_middleware(UploadBodyLimit, limit=settings.max_upload_bytes + 65_536)
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.settings = settings
    app.state.clock = resolved_clock
    app.state.transcription = transcription
    app.state.events = events_service
    app.state.knowledge = knowledge_service
    app.state.situation = situation_service

    def now():
        return utc(app.state.clock())

    def session_dependency():
        with sessions() as session:
            yield session

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        # Do not echo uploaded data or non-JSON numbers from rejected input.
        errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request, exc):
        logger.error("Database operation failed", exc_info=exc)
        return JSONResponse({"detail": "Storage is temporarily unavailable. Retry after checking the API and database."}, status_code=503)

    @app.exception_handler(OSError)
    async def media_storage_error(_request, exc):
        logger.error("Media storage operation failed", exc_info=exc)
        return JSONResponse({"detail": "Media storage is unavailable. Check free disk space and storage permissions."}, status_code=503)

    def incident_row(session: Session, incident_id: str, *, lock=False) -> Incident:
        query = select(Incident).where(Incident.id == incident_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        incident = session.scalar(query)
        if incident is None:
            raise HTTPException(404, "Incident not found")
        return incident

    def recordings_for(session: Session, incident_id: str) -> list[Recording]:
        return list(session.scalars(select(Recording).where(Recording.incident_id == incident_id).order_by(Recording.created_at, Recording.id)))

    def current_run(session: Session, incident: Incident) -> PlaybackRun:
        run = session.get(PlaybackRun, incident.active_run_id, populate_existing=True)
        if run is None:
            raise HTTPException(503, "Incident playback state is unavailable")
        return run

    def recording_view(recording: Recording, latest_analyzed: float | None = None) -> RecordingView:
        return RecordingView(
            id=recording.id, original_filename=recording.original_filename,
            camera_label=recording.camera_label, duration_seconds=recording.duration_seconds,
            start_offset_seconds=recording.start_offset_seconds, size_bytes=recording.size_bytes,
            media_url=f"/api/incidents/{recording.incident_id}/recordings/{recording.id}/media",
            latest_analyzed_time_seconds=latest_analyzed,
        )

    def detail(session: Session, incident: Incident) -> IncidentView:
        recordings = recordings_for(session, incident.id)
        run = current_run(session, incident)
        latest = transcription.latest_analyzed_by_recording(
            run_id=run.id,
            recording_ids=[r.id for r in recordings],
            session=session,
        )
        return IncidentView(
            id=incident.id, title=incident.title, context=incident.context,
            created_at=utc(incident.created_at),
            recordings=[recording_view(r, latest.get(r.id)) for r in recordings],
            playback=playback_view(run, recordings, now()),
        )

    def enqueue_transcription(
        session: Session,
        run: PlaybackRun,
        recordings: list[Recording],
        view: PlaybackView,
    ) -> None:
        """Release cutoff-eligible segments to the transcriber.

        Called after playback state is refreshed. Never sends unreleased media
        to the provider because eligibility is bounded by ``position_seconds``.
        """
        if not recordings:
            return
        try:
            transcription.enqueue_eligible(
                run_id=run.id,
                incident_position_seconds=view.position_seconds,
                recordings=recordings,
                session=session,
            )
        except Exception:  # pragma: no cover - defensive, do not fail playback
            logger.exception("Failed to enqueue transcription segments")

    def require_setup(run: PlaybackRun):
        if run.state != "paused" or run.position_seconds != 0:
            raise HTTPException(409, "Restart playback before uploading or changing alignment.")

    @app.get("/api/health")
    def health(session: Session = Depends(session_dependency)):
        database_ready = False
        try:
            session.execute(text("SELECT 1 FROM incidents LIMIT 1"))
            database_ready = True
        except SQLAlchemyError:
            session.rollback()
        media_ready = all(shutil.which(binary) for binary in (settings.ffmpeg_binary, settings.ffprobe_binary))
        storage_ready = settings.media_root.is_dir() and os.access(settings.media_root, os.W_OK)
        transcription_configured = bool(settings.xai_api_key)
        events_configured = bool(settings.openrouter_api_key)
        ready = database_ready and media_ready and storage_ready
        return JSONResponse(
            {
                "status": "ready" if ready else "unavailable",
                "database": database_ready,
                "media_tools": media_ready,
                "media_storage": storage_ready,
                "transcription_configured": transcription_configured,
                "events_configured": events_configured,
            },
            status_code=200 if ready else 503,
        )

    @app.get("/api/incidents", response_model=list[IncidentSummary])
    def list_incidents(session: Session = Depends(session_dependency)):
        return [IncidentSummary(id=i.id, title=i.title, context=i.context, created_at=utc(i.created_at))
                for i in session.scalars(select(Incident).order_by(Incident.created_at.desc(), Incident.id))]

    @app.post("/api/incidents", response_model=IncidentView, status_code=201)
    def create_incident(payload: IncidentCreate, session: Session = Depends(session_dependency)):
        with session.begin():
            timestamp = now()
            incident = Incident(id=str(uuid4()), title=payload.title, context=payload.context, created_at=timestamp)
            session.add(incident)
            session.flush()
            run = PlaybackRun(id=str(uuid4()), incident_id=incident.id, created_at=timestamp, anchor_at=timestamp, revision=0, position_seconds=0, state="paused")
            session.add(run)
            session.flush()
            incident.active_run_id = run.id
            session.flush()
            result = detail(session, incident)
        return result

    @app.get("/api/incidents/{incident_id}", response_model=IncidentView)
    def get_incident(incident_id: str, session: Session = Depends(session_dependency)):
        return detail(session, incident_row(session, incident_id, lock=True))

    def remove_stored_media(keys: list[str]) -> None:
        for key in keys:
            try:
                media_path(settings, key).unlink(missing_ok=True)
            except HTTPException:
                logger.warning("Skipped media cleanup for unexpected storage key %s", key)
            except OSError:
                logger.exception("Failed to remove media file %s", key)

    @app.delete("/api/incidents/{incident_id}", status_code=204)
    def delete_incident(incident_id: str, session: Session = Depends(session_dependency)):
        with session.begin():
            incident = incident_row(session, incident_id, lock=True)
            media_keys = [recording.storage_key for recording in recordings_for(session, incident_id)]
            run_ids = select(PlaybackRun.id).where(PlaybackRun.incident_id == incident_id)
            version_ids = select(KnowledgeVersion.id).where(KnowledgeVersion.run_id.in_(run_ids))
            item_ids = select(KnowledgeItem.id).where(KnowledgeItem.version_id.in_(version_ids))
            session.execute(delete(KnowledgeSupport).where(KnowledgeSupport.item_id.in_(item_ids)))
            session.execute(delete(KnowledgeItem).where(KnowledgeItem.version_id.in_(version_ids)))
            session.execute(delete(KnowledgeObservation).where(KnowledgeObservation.version_id.in_(version_ids)))
            session.execute(delete(KnowledgeVersion).where(KnowledgeVersion.run_id.in_(run_ids)))
            session.execute(delete(SituationReportVersion).where(SituationReportVersion.run_id.in_(run_ids)))
            session.execute(delete(EventHistory).where(EventHistory.run_id.in_(run_ids)))
            session.execute(delete(TranscriptSegment).where(TranscriptSegment.run_id.in_(run_ids)))
            incident.active_run_id = None
            session.flush()
            session.execute(delete(PlaybackRun).where(PlaybackRun.incident_id == incident_id))
            session.execute(delete(Recording).where(Recording.incident_id == incident_id))
            session.delete(incident)
        remove_stored_media(media_keys)
        return Response(status_code=204)

    @app.post("/api/incidents/{incident_id}/recordings", response_model=RecordingView, status_code=201)
    def upload_recording(
        incident_id: str,
        file: UploadFile = File(...),
        camera_label: str = Form(..., min_length=1, max_length=100),
        start_offset_seconds: float = Form(0, ge=0, le=86_400),
        session: Session = Depends(session_dependency),
    ):
        label = camera_label.strip()
        if not label:
            raise HTTPException(422, "A camera label is required")
        # Early checks give actionable responses without running costly decoding.
        with session.begin():
            incident = incident_row(session, incident_id)
            require_setup(current_run(session, incident))
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        temporary, size, duration = stage_upload(file, settings)
        final: Path | None = None
        try:
            with session.begin():
                incident = incident_row(session, incident_id, lock=True)
                run = current_run(session, incident)
                require_setup(run)
                recording = Recording(
                    id=str(uuid4()), incident_id=incident_id, original_filename=filename,
                    camera_label=label, storage_key=f"{uuid4().hex}.mp4",
                    duration_seconds=duration, start_offset_seconds=start_offset_seconds,
                    size_bytes=size, created_at=now(),
                )
                final = media_path(settings, recording.storage_key)
                temporary.replace(final)
                session.add(recording)
                run.revision += 1
                session.flush()
                result = recording_view(recording)
            return result
        except BaseException:
            if final is not None:
                final.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @app.patch("/api/incidents/{incident_id}/recordings/{recording_id}", response_model=RecordingView)
    def update_recording(incident_id: str, recording_id: str, payload: RecordingUpdate, session: Session = Depends(session_dependency)):
        if not payload.model_fields_set or any(getattr(payload, field) is None for field in payload.model_fields_set):
            raise HTTPException(422, "Supply a camera label or start offset; values cannot be null.")
        with session.begin():
            incident = incident_row(session, incident_id, lock=True)
            run = current_run(session, incident)
            require_setup(run)
            recording = session.scalar(select(Recording).where(Recording.id == recording_id, Recording.incident_id == incident_id))
            if recording is None:
                raise HTTPException(404, "Recording not found")
            if payload.camera_label is not None:
                recording.camera_label = payload.camera_label
            if payload.start_offset_seconds is not None:
                recording.start_offset_seconds = payload.start_offset_seconds
            run.revision += 1
            session.flush()
            result = recording_view(recording)
        return result

    @app.api_route("/api/incidents/{incident_id}/recordings/{recording_id}/media", methods=["GET", "HEAD"])
    def get_media(incident_id: str, recording_id: str, session: Session = Depends(session_dependency)):
        recording = session.scalar(select(Recording).where(Recording.id == recording_id, Recording.incident_id == incident_id))
        if recording is None:
            raise HTTPException(404, "Recording not found")
        path = media_path(settings, recording.storage_key)
        if not path.is_file():
            raise HTTPException(503, "The stored recording is missing. Check the media storage volume.")
        return FileResponse(path, media_type="video/mp4", filename=recording.original_filename, content_disposition_type="inline",
                            headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/incidents/{incident_id}/playback", response_model=PlaybackView)
    def get_playback(incident_id: str, session: Session = Depends(session_dependency)):
        with session.begin():
            incident = incident_row(session, incident_id, lock=True)
            run = current_run(session, incident)
            recordings = recordings_for(session, incident_id)
            view = playback_view(run, recordings, now())
            enqueue_transcription(session, run, recordings, view)
        return view

    @app.post("/api/incidents/{incident_id}/playback", response_model=PlaybackView)
    def control_playback(incident_id: str, payload: PlaybackCommand, session: Session = Depends(session_dependency)):
        with session.begin():
            incident = incident_row(session, incident_id, lock=True)
            run = current_run(session, incident)
            if run.revision != payload.expected_revision:
                raise HTTPException(409, "Playback changed. Refresh its state and try again.")
            recordings = recordings_for(session, incident_id)
            timestamp = now()
            current = playback_view(run, recordings, timestamp)
            if payload.action == "restart":
                run = PlaybackRun(id=str(uuid4()), incident_id=incident_id, created_at=timestamp, anchor_at=timestamp,
                                  revision=run.revision + 1, position_seconds=0, state="paused")
                session.add(run)
                session.flush()
                incident.active_run_id = run.id
            elif payload.action == "play":
                if not recordings:
                    raise HTTPException(409, "Upload at least one valid recording before playing.")
                if current.state == "ended":
                    raise HTTPException(409, "Playback has ended. Restart to begin a new run.")
                run.position_seconds = current.position_seconds
                run.anchor_at = timestamp
                run.state = "playing"
                run.revision += 1
            elif payload.action == "seek":
                # Seeking keeps the same run so transcripts and event history persist.
                target = min(max(0.0, float(payload.position_seconds or 0.0)), current.duration_seconds)
                run.position_seconds = target
                run.anchor_at = timestamp
                run.state = "ended" if current.duration_seconds > 0 and target >= current.duration_seconds else "paused"
                run.revision += 1
            else:
                run.position_seconds = current.position_seconds
                run.anchor_at = timestamp
                run.state = "ended" if current.state == "ended" else "paused"
                run.revision += 1
            session.flush()
            result = playback_view(run, recordings, timestamp)
            enqueue_transcription(session, run, recordings, result)
        return result

    @app.get("/api/incidents/{incident_id}/transcripts", response_model=TranscriptsView)
    def get_transcripts(incident_id: str, session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id, lock=True)
        run = current_run(session, incident)
        recordings = recordings_for(session, incident_id)
        recording_ids = [r.id for r in recordings]
        view = playback_view(run, recordings, now())
        segments = transcription.list_for_run(run_id=run.id, recording_ids=recording_ids, session=session)
        by_recording: dict[str, list[TranscriptSegmentView]] = {rid: [] for rid in recording_ids}
        for segment in segments:
            by_recording.setdefault(segment.recording_id, []).append(
                TranscriptSegmentView(
                    id=segment.id,
                    recording_id=segment.recording_id,
                    run_id=segment.run_id,
                    local_start_seconds=segment.local_start_seconds,
                    local_end_seconds=segment.local_end_seconds,
                    incident_start_seconds=segment.incident_start_seconds,
                    incident_end_seconds=segment.incident_end_seconds,
                    status=segment.status,
                    text=segment.text,
                    error=segment.error,
                    turns=transcript_turns_from_words(segment.words_json),
                )
            )
        latest = transcription.latest_analyzed_by_recording(
            run_id=run.id, recording_ids=recording_ids, session=session,
        )
        return TranscriptsView(
            run_id=run.id,
            incident_position_seconds=view.position_seconds,
            transcription_configured=bool(settings.xai_api_key),
            recordings=[
                RecordingTranscript(
                    recording_id=rid,
                    latest_analyzed_time_seconds=latest.get(rid),
                    segments=by_recording.get(rid, []),
                )
                for rid in recording_ids
            ],
        )

    @app.get("/api/incidents/{incident_id}/events")
    def get_events(incident_id: str, session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id)
        run = current_run(session, incident)
        return events_service.view(session, incident, run)

    @app.post("/api/incidents/{incident_id}/events")
    def update_events(incident_id: str, session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id)
        run = current_run(session, incident)
        if not settings.openrouter_api_key:
            raise HTTPException(503, "Set OPENROUTER_API_KEY in .env and restart the API.")
        run_id = run.id
        session.rollback()
        events_service.retry(run_id)
        events_service.process_incident(incident_id)
        incident = incident_row(session, incident_id)
        return events_service.view(session, incident, current_run(session, incident))

    @app.get("/api/incidents/{incident_id}/knowledge")
    def get_knowledge(incident_id: str, cutoff_seconds: float | None = Query(None, ge=0, allow_inf_nan=False),
                      session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id)
        return knowledge_service.view(session, incident, current_run(session, incident), cutoff_seconds)

    @app.post("/api/incidents/{incident_id}/knowledge", status_code=202)
    def build_knowledge(incident_id: str, force: bool = Query(False), session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id, lock=True)
        result = knowledge_service.enqueue(session, incident, current_run(session, incident), force=force)
        if result['status'] == 'queued':
            knowledge_service.begin(result['id'])
        return result

    @app.get("/api/incidents/{incident_id}/knowledge/{version_id}/sources/{segment_id}/thumbnail")
    def board_thumbnail(incident_id: str, version_id: str, segment_id: str, session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id)
        version = session.get(KnowledgeVersion, version_id)
        segment = session.get(TranscriptSegment, segment_id)
        if (not version or version.status != 'completed' or version.run_id != incident.active_run_id
                or not segment or segment.run_id != version.run_id or segment.status != 'completed'):
            raise HTTPException(404, 'Source frame unavailable')
        recording = session.get(Recording, segment.recording_id)
        if not recording or recording.incident_id != incident_id:
            raise HTTPException(404, 'Source frame unavailable')
        path = media_path(settings, recording.storage_key)
        # Representative frame from this exact source window; never generated imagery.
        timestamp = (segment.local_start_seconds + segment.local_end_seconds) / 2
        session.rollback()
        try:
            output = subprocess.run([settings.ffmpeg_binary, '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe',
                '-ss', str(timestamp), '-i', str(path), '-frames:v', '1', '-vf', 'scale=240:-2',
                '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1'], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(503, 'Source frame unavailable') from None
        if output.returncode or not output.stdout:
            raise HTTPException(404, 'Source frame unavailable')
        return Response(output.stdout, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})

    @app.get("/api/incidents/{incident_id}/situation-report")
    def get_situation_report(incident_id: str, session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id)
        return situation_service.view(session, incident, current_run(session, incident))

    @app.post("/api/incidents/{incident_id}/situation-report", status_code=202)
    def retry_situation_report(incident_id: str, session: Session = Depends(session_dependency)):
        incident = incident_row(session, incident_id)
        run = current_run(session, incident)
        situation_service.retry(session, run)
        return situation_service.view(session, incident, run)

    return app


app = create_app()
