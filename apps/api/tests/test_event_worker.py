import json
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
import threading
import time

import pytest

from bop_api.event_service import EventService
from bop_api.events import generate_events, normalize_events
from bop_api.models import EventHistory, PlaybackRun, Recording, TranscriptSegment


@pytest.fixture
def prepared(app, incident, clock):
    run_id = incident["playback"]["run_id"]
    with app.state.sessions() as session:
        session.add(Recording(id="cam", incident_id=incident["id"], original_filename="test.mp4",
                              camera_label="A", storage_key="worker-test.mp4", duration_seconds=60,
                              start_offset_seconds=2, size_bytes=100, created_at=clock()))
        session.get(PlaybackRun, run_id).position_seconds = 60
        session.commit()
    def add(segment_id, start=0):
        with app.state.sessions() as session:
            session.add(TranscriptSegment(id=segment_id, run_id=run_id, recording_id="cam",
                        local_start_seconds=start, local_end_seconds=start + 10,
                        incident_start_seconds=start + 2, incident_end_seconds=start + 12,
                        status="completed", text=f"Transcript {segment_id}", created_at=clock()))
            session.commit()
    service = EventService(replace(app.state.settings, openrouter_api_key="test"), app.state.sessions, clock)
    return service, add, incident["id"], run_id


def test_process_once_persists_and_catches_late_segments(app, prepared, clock):
    service, add, incident_id, run_id = prepared
    calls = []
    def generate(settings, segments, existing):
        calls.append([s.id for s in segments])
        return existing
    service.generator = generate
    add("later", 20)
    assert service.process_incident(incident_id)
    # A different worker/API instance reads the saved checkpoint.
    fresh = EventService(service.settings, app.state.sessions, clock, generate)
    assert not fresh.process_incident(incident_id)
    add("earlier", 0)
    assert fresh.process_incident(incident_id)
    assert calls == [["later"], ["earlier"]]
    with app.state.sessions() as session:
        assert set(json.loads(session.get(EventHistory, run_id).processed_json)) == {"earlier", "later"}


def test_failure_backoff_and_manual_retry(app, prepared, clock):
    service, add, incident_id, run_id = prepared
    add("first")
    calls = []
    def fail(*args):
        calls.append(1)
        raise RuntimeError("secret")
    service.generator = fail
    assert not service.process_incident(incident_id)
    assert not service.process_incident(incident_id)
    assert len(calls) == 1
    for _ in range(2):
        clock.advance(120)
        assert not service.process_incident(incident_id)
    clock.advance(120)
    assert not service.process_incident(incident_id)
    assert len(calls) == 3
    with app.state.sessions() as session:
        row = session.get(EventHistory, run_id)
        assert row.processed_json == "[]"
        assert "secret" not in row.error
    service.retry(run_id)
    service.generator = lambda *args: []
    assert service.process_incident(incident_id)


def test_stale_attempt_count_without_error_still_processes(app, prepared):
    service, add, incident_id, run_id = prepared
    add("first")
    with app.state.sessions() as session:
        session.add(EventHistory(run_id=run_id, events_json="[]", attempts=3, error=None))
        session.commit()
    service.generator = lambda *args: [{"id": "e", "segment_id": "first", "recording_id": "cam",
                                        "timestamp_seconds": 2, "local_seconds": 0, "title": "A report",
                                        "status": "current"}]
    assert service.process_incident(incident_id)


def test_lease_prevents_duplicate_calls_and_recovers_after_crash(app, prepared, clock):
    service, add, incident_id, run_id = prepared
    add("first")
    with app.state.sessions() as session:
        session.add(EventHistory(run_id=run_id, events_json="[]", lease_token="old",
                                lease_until=clock() + timedelta(seconds=10)))
        session.commit()
    service.generator = lambda *args: []
    assert not service.process_incident(incident_id)
    clock.advance(11)
    def generate(*args):
        assert not service.process_incident(incident_id)
        return []
    service.generator = generate
    assert service.process_incident(incident_id)


def test_restart_while_provider_runs_discards_result(app, client, prepared):
    service, add, incident_id, run_id = prepared
    add("first")
    def generate(*args):
        playback = client.get(f"/api/incidents/{incident_id}/playback").json()
        assert client.post(f"/api/incidents/{incident_id}/playback", json={
            "action": "restart", "expected_revision": playback["revision"]}).status_code == 200
        return [{"title": "Must not appear"}]
    service.generator = generate
    assert not service.process_incident(incident_id)
    assert client.get(f"/api/incidents/{incident_id}/events").json()["events"] == []
    with app.state.sessions() as session:
        assert session.get(EventHistory, run_id).processed_json == "[]"


def test_new_claim_can_be_colored_in_same_batch(monkeypatch, settings):
    sources = [SimpleNamespace(id="one", recording_id="cam", text="Nobody inside", incident_start_seconds=2, local_start_seconds=0),
               SimpleNamespace(id="two", recording_id="cam", text="Upstairs not checked", incident_start_seconds=12, local_start_seconds=10)]
    changes = {"new_events": [{"id": "new-1", "segment_id": "one", "title": "Speaker reports nobody inside"}],
               "updates": [{"event_id": "new-1", "supporting_segment_id": "two", "status": "outdated",
                            "reason": "Upstairs has not been checked, so the report is incomplete."}]}
    def response(*args, **kwargs):
        return StringIO(json.dumps({"choices": [{"message": {"content": json.dumps(changes)}}]}))
    monkeypatch.setattr("bop_api.events.urlopen", response)
    result = generate_events(settings, sources)
    assert result[0]["status"] == "outdated"
    assert result[0]["timestamp_seconds"] == 2
    assert result[0]["status_local_seconds"] == 10
    assert result[0]["assessment_history"][0]["source_text"] == "Upstairs not checked"
    changes["updates"][0]["supporting_segment_id"] = "invented"
    ignored = generate_events(settings, sources)
    assert ignored[0]["status"] == "current"


def test_invalid_update_does_not_discard_valid_new_event(monkeypatch, settings):
    sources = [SimpleNamespace(id="source", recording_id="cam", text="A responder requests an ambulance",
                               incident_start_seconds=12, local_start_seconds=10)]
    changes = {"new_events": [{"id": "new-1", "segment_id": "source", "title": "Responder requests an ambulance"}],
               "updates": [{"event_id": "invented", "supporting_segment_id": "source",
                            "status": "outdated", "reason": "Unsupported reference"}]}
    monkeypatch.setattr("bop_api.events.urlopen", lambda *args, **kwargs: StringIO(json.dumps(
        {"choices": [{"message": {"content": json.dumps(changes)}}]})))
    result = generate_events(settings, sources)
    assert len(result) == 1
    assert result[0]["title"] == "Responder requests an ambulance"
    assert result[0]["status"] == "current"


def test_unique_source_segment_is_accepted_as_event_alias(monkeypatch, settings):
    sources = [SimpleNamespace(id="later", recording_id="cam", text="It is a phone, not a knife",
                               incident_start_seconds=12, local_start_seconds=10)]
    existing = [{"id": "uuid", "segment_id": "earlier", "recording_id": "cam", "timestamp_seconds": 2,
                 "local_seconds": 0, "title": "Speaker reports a knife", "status": "current"}]
    changes = {"new_events": [], "updates": [{"event_id": "earlier", "supporting_segment_id": "later",
               "status": "disproven", "reason": "Speaker corrects the object to a phone."}]}
    monkeypatch.setattr("bop_api.events.urlopen", lambda *args, **kwargs: StringIO(json.dumps(
        {"choices": [{"message": {"content": json.dumps(changes)}}]})))
    result = generate_events(settings, sources, existing)
    assert result[0]["status"] == "disproven"


def test_provider_content_blocks_and_http_errors(monkeypatch, settings):
    source = SimpleNamespace(id="source", recording_id="cam", text="Help arrived",
                             incident_start_seconds=2, local_start_seconds=0)
    changes = {"updates": [], "new_events": [{"id": "new-1", "segment_id": "source", "title": "Help reportedly arrives"}]}

    class BlockResponse:
        def __enter__(self):
            return StringIO(json.dumps({"choices": [{"message": {"content": [
                {"type": "text", "text": json.dumps(changes)}
            ]}}]}))
        def __exit__(self, *args):
            pass

    monkeypatch.setattr("bop_api.events.urlopen", lambda *args, **kwargs: BlockResponse())
    assert generate_events(settings, [source])[0]["title"] == "Help reportedly arrives"

    class Rejected:
        def __init__(self):
            from io import BytesIO
            from urllib.error import HTTPError
            self.error = HTTPError(
                "https://openrouter.ai/api/v1/chat/completions", 400, "Bad Request",
                hdrs={}, fp=BytesIO(json.dumps({
                    "error": {"message": "Invalid schema for structured outputs"}
                }).encode()),
            )
        def __enter__(self):
            raise self.error
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("bop_api.events.urlopen", lambda *args, **kwargs: Rejected())
    with pytest.raises(ValueError, match="structured JSON"):
        generate_events(settings, [source])


def test_legacy_ids_are_stable():
    legacy = [{"segment_id": "source", "title": "A report"}]
    assert normalize_events(legacy) == normalize_events(legacy)


def test_background_worker_processes_without_http_request(app, prepared, clock):
    service, add, incident_id, run_id = prepared
    add("automatic")
    invoked = threading.Event()
    def generate(*args):
        invoked.set()
        return []
    worker = EventService(replace(service.settings, event_worker_interval_seconds=0.02),
                          app.state.sessions, clock, generate)
    worker.start()
    try:
        assert invoked.wait(timeout=3), "The worker did not call the provider automatically"
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with app.state.sessions() as session:
                if json.loads(session.get(EventHistory, run_id).processed_json) == ["automatic"]:
                    return
            time.sleep(0.02)
        pytest.fail("The worker did not persist its automatic result")
    finally:
        worker.stop()
