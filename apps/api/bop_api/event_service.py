"""Incremental event worker. Progress and ownership survive API restarts."""
from datetime import timedelta
import json
import logging
import threading
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from .events import generate_events, normalize_events, EventProviderError
from .models import EventHistory, Incident, PlaybackRun, Recording, TranscriptSegment
from .playback import playback_view, utc

logger = logging.getLogger(__name__)


class EventService:
    def __init__(self, settings, sessions, clock, generator=None):
        self.settings, self.sessions, self.clock = settings, sessions, clock
        self.generator = generator or generate_events
        self._stop = threading.Event()
        self._worker = None

    def start(self):
        if not self.settings.openrouter_api_key or self.settings.event_worker_interval_seconds <= 0:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="event-worker", daemon=True)
        self._worker.start()

    def stop(self):
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            try:
                progressed = self.process_pending()
            except Exception:
                progressed = False
                logger.exception("Event worker iteration failed; it will retry on the next interval.")
            # Drain another batch immediately after success, while retaining
            # the normal poll/backoff when idle, leased elsewhere or failed.
            if not progressed and self._stop.wait(self.settings.event_worker_interval_seconds):
                return

    def _eligible(self, session, incident, run):
        recordings = list(session.scalars(select(Recording).where(Recording.incident_id == incident.id)))
        cutoff = playback_view(run, recordings, self.clock()).position_seconds
        return list(session.scalars(select(TranscriptSegment).where(
            TranscriptSegment.run_id == run.id,
            TranscriptSegment.status == "completed",
            TranscriptSegment.incident_end_seconds <= cutoff,
        ).order_by(TranscriptSegment.incident_start_seconds, TranscriptSegment.id)))

    def view(self, session, incident, run):
        history = session.get(EventHistory, run.id)
        done = set(json.loads(history.processed_json)) if history else set()
        pending = sum(segment.id not in done for segment in self._eligible(session, incident, run))
        processing = bool(history and history.lease_until and utc(history.lease_until) > self.clock())
        configured = bool(self.settings.openrouter_api_key)
        state = ("disabled" if not configured else "processing" if processing else
                 "failed" if history and history.error and history.attempts >= 3 else
                 "retrying" if history and history.error else "queued" if pending else "idle")
        return {"run_id": run.id, "configured": configured, "model": self.settings.openrouter_model,
                "state": state, "pending_segments": pending, "processed_segments": len(done),
                "error": history.error if history else None,
                "events": normalize_events(json.loads(history.events_json)) if history else []}

    def retry(self, run_id):
        with self.sessions() as session:
            session.execute(update(EventHistory).where(
                EventHistory.run_id == run_id,
                or_(EventHistory.lease_until.is_(None), EventHistory.lease_until <= self.clock()),
            ).values(attempts=0, retry_at=None, error=None).execution_options(synchronize_session=False))
            session.commit()

    def process_pending(self):
        if not self.settings.openrouter_api_key:
            return
        with self.sessions() as session:
            ids = list(session.scalars(select(Incident.id)))
        progressed = False
        for incident_id in ids:
            if self._stop.is_set():
                break
            progressed = self.process_incident(incident_id) or progressed
        return progressed

    def process_incident(self, incident_id):
        if not self.settings.openrouter_api_key:
            return False
        token = str(uuid4())
        now = self.clock()
        with self.sessions() as session:
            incident = session.get(Incident, incident_id)
            if incident is None or not incident.active_run_id:
                return False
            run_id = incident.active_run_id
            run = session.get(PlaybackRun, run_id)
            history = session.get(EventHistory, run_id)
            if history is None:
                history = EventHistory(run_id=run_id, events_json="[]")
                session.add(history)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    return False  # Another worker created this run's progress.
            done = set(json.loads(history.processed_json))
            # Track identities, not a time watermark: delayed transcripts still get processed.
            segments = [s for s in self._eligible(session, incident, run) if s.id not in done][:8]
            if not segments:
                return False
            existing = normalize_events(json.loads(history.events_json))
            # Atomic lease prevents browser retries and other API workers from duplicating work.
            claimed = session.execute(update(EventHistory).where(
                EventHistory.run_id == run_id,
                or_(EventHistory.attempts < 3, EventHistory.error.is_(None)),
                EventHistory.processed_json == history.processed_json,
                EventHistory.events_json == history.events_json,
                or_(EventHistory.lease_until.is_(None), EventHistory.lease_until <= now),
                or_(EventHistory.retry_at.is_(None), EventHistory.retry_at <= now),
            ).values(lease_token=token, lease_until=now + timedelta(seconds=240)).execution_options(synchronize_session=False))
            if claimed.rowcount != 1:
                session.rollback()
                return False
            # Detach before commit expires objects; no DB transaction during network work.
            session.expunge_all()
            session.commit()
        try:
            events = self.generator(self.settings, segments, existing)
            error = None
        except EventProviderError as exc:
            logger.warning("Event analysis failed: %s", exc)
            events = None
            error = str(exc)[:500]
        except Exception:
            logger.exception("Event analysis failed")
            events = None
            error = "Event analysis failed. Check the OpenRouter model, key or credits. Saved events are retained."
        with self.sessions() as session:
            # Lock the incident in the same order as playback restart.
            incident = session.scalar(select(Incident).where(Incident.id == incident_id).with_for_update())
            history = session.get(EventHistory, run_id)
            if history is None or history.lease_token != token:
                return False
            if incident is None or incident.active_run_id != run_id:
                session.execute(update(EventHistory).where(EventHistory.run_id == run_id,
                    EventHistory.lease_token == token).values(lease_token=None, lease_until=None))
                session.commit()
                return False
            values = {"lease_token": None, "lease_until": None}
            if error:
                attempts = history.attempts + 1
                values.update(error=error, attempts=attempts,
                              retry_at=self.clock() + timedelta(seconds=15 * 2 ** (attempts - 1)))
            else:
                values.update(events_json=json.dumps(events),
                              processed_json=json.dumps(sorted(done | {s.id for s in segments})),
                              attempts=0, error=None, retry_at=None)
            session.execute(update(EventHistory).where(EventHistory.run_id == run_id,
                EventHistory.lease_token == token).values(**values))
            session.commit()
        return error is None
