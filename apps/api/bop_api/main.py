from contextlib import asynccontextmanager
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException

from .config import Settings
from .database import make_database
from .media import media_path, stage_upload
from .models import Incident, PlaybackRun, Recording
from .playback import playback_view, utc
from .schemas import IncidentCreate, IncidentSummary, IncidentView, PlaybackCommand, PlaybackView, RecordingUpdate, RecordingView

logger = logging.getLogger(__name__)


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


def create_app(settings: Settings | None = None, clock=None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine, sessions = make_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app):
        settings.media_root.mkdir(parents=True, exist_ok=True)
        yield
        engine.dispose()

    app = FastAPI(title="Bodycam incident foundation", version="0.1.0", lifespan=lifespan)
    app.add_middleware(UploadBodyLimit, limit=settings.max_upload_bytes + 65_536)
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.settings = settings
    app.state.clock = clock or (lambda: datetime.now(UTC))

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

    def recording_view(recording: Recording) -> RecordingView:
        return RecordingView(
            id=recording.id, original_filename=recording.original_filename,
            camera_label=recording.camera_label, duration_seconds=recording.duration_seconds,
            start_offset_seconds=recording.start_offset_seconds, size_bytes=recording.size_bytes,
            media_url=f"/api/incidents/{recording.incident_id}/recordings/{recording.id}/media",
        )

    def detail(session: Session, incident: Incident) -> IncidentView:
        recordings = recordings_for(session, incident.id)
        return IncidentView(
            id=incident.id, title=incident.title, context=incident.context,
            created_at=utc(incident.created_at), recordings=[recording_view(r) for r in recordings],
            playback=playback_view(current_run(session, incident), recordings, now()),
        )

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
        ready = database_ready and media_ready and storage_ready
        return JSONResponse(
            {"status": "ready" if ready else "unavailable", "database": database_ready, "media_tools": media_ready, "media_storage": storage_ready},
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
            if len(recordings_for(session, incident_id)) >= 3:
                raise HTTPException(409, "An incident supports at most three recordings.")
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        temporary, size, duration = stage_upload(file, settings)
        final: Path | None = None
        try:
            with session.begin():
                incident = incident_row(session, incident_id, lock=True)
                run = current_run(session, incident)
                require_setup(run)
                if len(recordings_for(session, incident_id)) >= 3:
                    raise HTTPException(409, "An incident supports at most three recordings.")
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
        incident = incident_row(session, incident_id, lock=True)
        return playback_view(current_run(session, incident), recordings_for(session, incident_id), now())

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
                if len(recordings) < 2:
                    raise HTTPException(409, "Upload at least two valid recordings before playing.")
                if current.state == "ended":
                    raise HTTPException(409, "Playback has ended. Restart to begin a new run.")
                run.position_seconds = current.position_seconds
                run.anchor_at = timestamp
                run.state = "playing"
                run.revision += 1
            else:
                run.position_seconds = current.position_seconds
                run.anchor_at = timestamp
                run.state = "ended" if current.state == "ended" else "paused"
                run.revision += 1
            session.flush()
            result = playback_view(run, recordings, timestamp)
        return result

    return app


app = create_app()
