import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bop_api.events import generate_events
from bop_api.models import TranscriptSegment


def test_provider_sources_and_timestamps(monkeypatch, settings):
    source = SimpleNamespace(id="source", recording_id="camera", text="Help has arrived.",
                             incident_start_seconds=12, local_start_seconds=10)
    class Response:
        def __enter__(self):
            from io import StringIO
            return StringIO(json.dumps({"choices": [{"message": {"content": json.dumps({
                "updates": [], "new_events": [{"id": "new-1", "segment_id": "source", "title": "Help reportedly arrives"}]
            })}}]}))
        def __exit__(self, *args):
            pass
    def respond(request, timeout):
        payload = json.loads(request.data)
        assert json.loads(payload["messages"][1]["content"])["transcript_sources"][0]["text"] == source.text
        schema = json.dumps(payload["response_format"]["json_schema"]["schema"])
        assert "minLength" not in schema and "maxItems" not in schema
        assert timeout == 180
        return Response()
    monkeypatch.setattr("bop_api.events.urlopen", respond)
    events = generate_events(settings, [source])
    assert events[0]["timestamp_seconds"] == 12
    assert events[0]["local_seconds"] == 10
    source.id = "different"
    with pytest.raises(ValueError, match="unknown source"):
        generate_events(settings, [source])


def test_existing_events_keep_timestamp_and_receive_status(monkeypatch, settings):
    source = SimpleNamespace(id="later", recording_id="camera", text="No help has arrived.",
                             incident_start_seconds=20, local_start_seconds=20)
    existing = [{"id": "event-1", "segment_id": "earlier", "recording_id": "camera",
                 "timestamp_seconds": 10, "local_seconds": 10, "title": "Help arrives", "status": "current"}]
    class Response:
        def __enter__(self):
            from io import StringIO
            return StringIO(json.dumps({"choices": [{"message": {"content": json.dumps({
                "updates": [{"event_id": "event-1", "supporting_segment_id": "later", "status": "disproven", "reason": "Later speaker corrects the arrival report."}], "new_events": []
            })}}]}))
        def __exit__(self, *args):
            pass
    monkeypatch.setattr("bop_api.events.urlopen", lambda *args, **kwargs: Response())
    events = generate_events(settings, [source], existing)
    assert events[0]["timestamp_seconds"] == 10
    assert events[0]["title"] == "Help arrives"
    assert events[0]["status"] == "disproven"
    assert events[0]["status_timestamp_seconds"] == 20


def test_history_configuration(client, incident):
    path = f"/api/incidents/{incident['id']}/events"
    assert client.get(path).json()["events"] == []
    assert client.post(path).status_code == 503


def test_history_persistence_failure_and_restart(app, client, incident, monkeypatch, clock):
    from fastapi.testclient import TestClient
    from bop_api.main import create_app
    from bop_api.models import Recording
    run_id = incident["playback"]["run_id"]
    with app.state.sessions() as session:
        session.add(Recording(id="camera", incident_id=incident["id"], original_filename="a.mp4",
                              camera_label="A", storage_key="test.mp4", duration_seconds=30,
                              start_offset_seconds=2, size_bytes=100, created_at=clock()))
        session.flush()
        session.add(TranscriptSegment(id="source", run_id=run_id, recording_id="camera",
                                     local_start_seconds=0, local_end_seconds=10,
                                     incident_start_seconds=2, incident_end_seconds=12,
                                     status="completed", text="Help arrived", created_at=clock()))
        session.add(TranscriptSegment(id="future", run_id=run_id, recording_id="camera",
                                     local_start_seconds=10, local_end_seconds=20,
                                     incident_start_seconds=12, incident_end_seconds=22,
                                     status="completed", text="Future information", created_at=clock()))
        from bop_api.models import PlaybackRun
        session.get(PlaybackRun, run_id).position_seconds = 12
        session.commit()
    configured = create_app(replace(app.state.settings, openrouter_api_key="test"), clock)
    result = [{"id": "event-1", "segment_id": "source", "recording_id": "camera", "timestamp_seconds": 2,
               "local_seconds": 0, "title": "Help reportedly arrives", "status": "current"}]
    def generate(settings, segments, existing_events):
        assert [s.id for s in segments] == ["source"]
        assert existing_events == []
        return result
    configured.state.events.generator = generate
    path = f"/api/incidents/{incident['id']}/events"
    with TestClient(configured) as api:
        assert api.get(path).json()["state"] == "queued"
        assert configured.state.events.process_incident(incident['id']) is True
        assert api.get(path).json()["events"] == result
        assert api.get(path).json()["processed_segments"] == 1
        assert configured.state.events.process_incident(incident['id']) is False
        with app.state.sessions() as session:
            session.get(PlaybackRun, run_id).position_seconds = 22
            session.commit()
        def fail(*args):
            raise ValueError("secret provider body")
        configured.state.events.generator = fail
        assert configured.state.events.process_incident(incident['id']) is False
        response = api.get(path)
        assert response.json()["state"] == "retrying"
        assert "secret" not in response.text
        assert api.get(path).json()["events"] == result
        configured.state.events.generator = lambda *args: result
        posted = api.post(path).json()
        assert posted["state"] == "idle"
        assert posted["events"] == result
        playback = api.get(f"/api/incidents/{incident['id']}/playback").json()
        assert api.post(f"/api/incidents/{incident['id']}/playback", json={
            "action": "restart", "expected_revision": playback["revision"]}).status_code == 200
        assert api.get(path).json()["events"] == []
