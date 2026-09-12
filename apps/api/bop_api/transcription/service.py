"""In-process transcription service.

Two responsibilities, kept intentionally simple for the local demo:

* ``enqueue_eligible`` runs synchronously during playback reads/controls,
  inserting ``queued`` rows for every window fully released by the cutoff.
* ``process_pending`` (called by a background thread, or directly by tests)
  claims released rows in time order and processes bounded concurrent audio
  windows, persisting each result independently.

Runs are isolated: rows are keyed by ``run_id``, so restart-created runs
never see the previous run's segments.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import tempfile
import threading
from typing import Callable
from uuid import uuid4

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..media import media_path
from ..models import PlaybackRun, Recording, TranscriptSegment
from .audio import AudioExtractionError, extract_audio_window
from .eligibility import eligible_windows
from .transcriber import Transcriber, TranscriberError

logger = logging.getLogger(__name__)


class TranscriptionService:
    def __init__(
        self,
        settings: Settings,
        sessions: sessionmaker,
        transcriber: Transcriber,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._transcriber = transcriber
        self._clock = clock or (lambda: datetime.now(UTC))
        self._worker: threading.Thread | None = None
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._processing_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._settings.transcription_worker_interval_seconds <= 0:
            return
        if self._worker is not None:
            return
        self._requeue_stuck_processing()
        self._stop.clear()
        self._wakeup.clear()
        thread = threading.Thread(
            target=self._run_forever,
            name="transcription-worker",
            daemon=True,
        )
        self._worker = thread
        thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wakeup.set()
        thread = self._worker
        self._worker = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def kick(self) -> None:
        """Signal the background worker there may be new work."""
        self._wakeup.set()

    # -- enqueue -------------------------------------------------------------

    def enqueue_eligible(
        self,
        *,
        run_id: str,
        incident_position_seconds: float,
        recordings: list[Recording],
        session: Session | None = None,
    ) -> int:
        """Insert queued rows for any newly-eligible windows. Idempotent.

        Returns the number of rows added. When ``session`` is provided, rows
        are inserted through the caller's session using savepoints so the
        SQLite writer lock is not re-acquired by a second connection.
        """
        added = 0
        segment_seconds = self._settings.transcription_segment_seconds
        planned: list[tuple[Recording, object]] = []
        for recording in recordings:
            for window in eligible_windows(
                incident_position_seconds=incident_position_seconds,
                start_offset_seconds=recording.start_offset_seconds,
                duration_seconds=recording.duration_seconds,
                segment_seconds=segment_seconds,
            ):
                planned.append((recording, window))
        if not planned:
            return 0

        def try_insert(active_session: Session, recording, window) -> bool:
            candidate = TranscriptSegment(
                id=str(uuid4()),
                run_id=run_id,
                recording_id=recording.id,
                local_start_seconds=window.local_start,
                local_end_seconds=window.local_end,
                incident_start_seconds=window.incident_start,
                incident_end_seconds=window.incident_end,
                status="queued",
                created_at=self._clock(),
            )
            active_session.add(candidate)
            try:
                active_session.flush()
                return True
            except IntegrityError:
                # Already enqueued; another poll released the same window.
                return False

        if session is not None:
            for recording, window in planned:
                try:
                    with session.begin_nested():
                        if try_insert(session, recording, window):
                            added += 1
                except IntegrityError:
                    continue
                except SQLAlchemyError:
                    logger.exception(
                        "Failed to enqueue transcript segment %s [%s, %s)",
                        recording.id,
                        window.local_start,
                        window.local_end,
                    )
                    continue
        else:
            for recording, window in planned:
                try:
                    with self._sessions() as owned, owned.begin():
                        if try_insert(owned, recording, window):
                            added += 1
                except IntegrityError:
                    continue
                except SQLAlchemyError:
                    logger.exception(
                        "Failed to enqueue transcript segment %s [%s, %s)",
                        recording.id,
                        window.local_start,
                        window.local_end,
                    )
                    continue
        if added:
            self.kick()
        return added

    # -- processing ----------------------------------------------------------

    def process_pending(self, *, max_segments: int | None = None) -> int:
        """Drain queued segments. Safe to call from tests directly.

        Returns the number of segments processed in this call.
        """
        # Claims stay serialized; independent audio/provider work can overlap.
        with self._processing_lock, ThreadPoolExecutor(
            max_workers=self._settings.transcription_concurrency,
            thread_name_prefix="transcribe",
        ) as pool:
            processed = submitted = 0
            pending = set()
            while True:
                while (not self._stop.is_set()
                       and len(pending) < self._settings.transcription_concurrency
                       and (max_segments is None or submitted < max_segments)):
                    claim = self._claim_next()
                    if claim is None:
                        break
                    segment_id, recording_id, storage_key, local_start, local_end = claim
                    pending.add(pool.submit(
                        self._process_one, segment_id=segment_id, recording_id=recording_id,
                        storage_key=storage_key, local_start=local_start, local_end=local_end,
                    ))
                    submitted += 1
                if not pending:
                    return processed
                completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
                    processed += 1

    def _claim_next(self) -> tuple[str, str, str, float, float] | None:
        with self._sessions() as session, session.begin():
            segment = session.scalar(
                select(TranscriptSegment)
                .where(TranscriptSegment.status == "queued")
                .order_by(TranscriptSegment.created_at, TranscriptSegment.incident_start_seconds, TranscriptSegment.id)
                .limit(1)
            )
            if segment is None:
                return None
            recording = session.get(Recording, segment.recording_id)
            if recording is None:
                segment.status = "failed"
                segment.error = "Recording is missing"
                segment.completed_at = self._clock()
                return None
            claimed = session.execute(update(TranscriptSegment).where(
                TranscriptSegment.id == segment.id, TranscriptSegment.status == "queued",
            ).values(status="processing").execution_options(synchronize_session=False))
            if claimed.rowcount != 1:
                return None
            return (
                segment.id,
                recording.id,
                recording.storage_key,
                segment.local_start_seconds,
                segment.local_end_seconds,
            )

    def _process_one(
        self,
        *,
        segment_id: str,
        recording_id: str,
        storage_key: str,
        local_start: float,
        local_end: float,
    ) -> None:
        del recording_id
        status = "completed"
        text: str | None = None
        words_json: str | None = None
        error: str | None = None
        try:
            source = media_path(self._settings, storage_key)
            if not source.is_file():
                raise TranscriberError(
                    "The stored recording is missing. Check the media storage volume."
                )
            with tempfile.TemporaryDirectory(prefix="bop-stt-") as scratch:
                audio_path, has_audio = extract_audio_window(
                    self._settings,
                    source,
                    local_start=local_start,
                    local_end=local_end,
                    scratch_dir=Path(scratch),
                    timeout_seconds=max(
                        self._settings.media_validation_timeout_seconds,
                        (local_end - local_start) * 4,
                    ),
                )
                if not has_audio:
                    status = "empty"
                else:
                    try:
                        result = self._transcriber.transcribe(audio_path)
                    finally:
                        audio_path.unlink(missing_ok=True)
                    if result.is_empty:
                        status = "empty"
                    else:
                        text = result.text
                        if result.words:
                            words_json = json.dumps(result.words)
        except AudioExtractionError as exc:
            status = "failed"
            error = str(exc)
        except TranscriberError as exc:
            status = "failed"
            error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            status = "failed"
            error = f"Unexpected transcription error: {exc}"
            logger.exception("Unexpected transcription failure for %s", segment_id)

        completed_at = self._clock()
        with self._sessions() as session, session.begin():
            session.execute(
                update(TranscriptSegment)
                .where(TranscriptSegment.id == segment_id)
                .values(
                    status=status,
                    text=text,
                    words_json=words_json,
                    error=error[:500] if error else None,
                    completed_at=completed_at,
                )
            )

    def _requeue_stuck_processing(self) -> None:
        try:
            with self._sessions() as session, session.begin():
                session.execute(
                    update(TranscriptSegment)
                    .where(TranscriptSegment.status == "processing")
                    .values(status="queued", error=None)
                )
        except SQLAlchemyError:
            logger.exception("Could not requeue stuck 'processing' transcript segments")

    def _run_forever(self) -> None:
        interval = self._settings.transcription_worker_interval_seconds
        while not self._stop.is_set():
            triggered = self._wakeup.wait(interval)
            self._wakeup.clear()
            if self._stop.is_set():
                return
            try:
                self.process_pending()
            except Exception:  # pragma: no cover - keep the worker alive
                logger.exception("Transcription worker iteration failed")
            del triggered

    # -- read helpers --------------------------------------------------------

    def list_for_run(
        self,
        *,
        run_id: str,
        recording_ids: list[str],
        session: Session | None = None,
    ) -> list[TranscriptSegment]:
        if not recording_ids:
            return []
        query = (
            select(TranscriptSegment)
            .where(
                and_(
                    TranscriptSegment.run_id == run_id,
                    TranscriptSegment.recording_id.in_(recording_ids),
                )
            )
            .order_by(
                TranscriptSegment.recording_id,
                TranscriptSegment.incident_start_seconds,
                TranscriptSegment.id,
            )
        )
        if session is not None:
            return list(session.scalars(query).all())
        with self._sessions() as owned:
            return list(owned.scalars(query).all())

    def latest_analyzed_by_recording(
        self,
        *,
        run_id: str,
        recording_ids: list[str],
        session: Session | None = None,
    ) -> dict[str, float]:
        """Return contiguous analyzed time; unfinished gaps never advance it."""
        if not recording_ids:
            return {}
        query = (
            select(
                TranscriptSegment.recording_id,
                TranscriptSegment.local_start_seconds,
                TranscriptSegment.local_end_seconds,
                TranscriptSegment.incident_end_seconds,
                TranscriptSegment.status,
            )
            .where(
                and_(
                    TranscriptSegment.run_id == run_id,
                    TranscriptSegment.recording_id.in_(recording_ids),
                )
            )
        )
        if session is not None:
            rows = session.execute(query).all()
        else:
            with self._sessions() as owned:
                rows = owned.execute(query).all()
        latest: dict[str, float] = {}
        local_ends: dict[str, float] = {}
        blocked = set()
        for recording_id, start, end, incident_end, status in sorted(rows):
            if recording_id in blocked:
                continue
            if status not in ("completed", "empty") or start > local_ends.get(recording_id, 0) + 1e-6:
                blocked.add(recording_id)
                continue
            local_ends[recording_id] = end
            latest[recording_id] = float(incident_end)
        return latest


def _run_finished(run: PlaybackRun) -> bool:  # pragma: no cover - kept for readability
    return run.state == "ended"
