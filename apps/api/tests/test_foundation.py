from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import subprocess

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from bop_api.main import create_app
from bop_api.models import (
    EventHistory,
    KnowledgeItem,
    KnowledgeObservation,
    KnowledgeSupport,
    KnowledgeVersion,
    PlaybackRun,
    Recording,
    SituationReportVersion,
    TranscriptSegment,
)
from conftest import upload


def control(client, incident_id, action, revision):
    return client.post(f"/api/incidents/{incident_id}/playback", json={"action": action, "expected_revision": revision})


def test_create_validation_and_empty_processing(client, incident):
    assert incident["recordings"] == []
    assert incident["playback"]["state"] == "paused"
    assert incident["playback"]["position_seconds"] == 0
    assert incident["playback"]["revision"] == 0
    assert client.get("/api/incidents").json()[0]["id"] == incident["id"]
    assert client.post("/api/incidents", json={"title": "   "}).status_code == 422
    assert client.post("/api/incidents", json={"title": "A", "unexpected": 1}).status_code == 422
    assert client.get("/api/incidents/missing").status_code == 404
    assert control(client, incident["id"], "play", 0).status_code == 409


def test_media_round_trip_range_and_incident_isolation(client, prepared, media_fixture):
    recording = prepared["recordings"][0]
    assert recording["validation_status"] == "ready"
    assert recording["processing_status"] == "transcribing"
    assert recording["latest_analyzed_time_seconds"] is None
    assert recording["size_bytes"] == len(media_fixture)
    assert recording["duration_seconds"] == pytest.approx(3, abs=0.2)
    media = client.get(recording["media_url"])
    assert media.status_code == 200
    assert media.content == media_fixture
    partial = client.get(recording["media_url"], headers={"Range": "bytes=13-92"})
    assert partial.status_code == 206
    assert partial.content == media_fixture[13:93]
    assert partial.headers["content-range"] == f"bytes 13-92/{len(media_fixture)}"
    assert client.head(recording["media_url"]).headers["content-length"] == str(len(media_fixture))
    assert client.get(recording["media_url"], headers={"Range": "bytes=99999999-"}).status_code == 416
    other = client.post("/api/incidents", json={"title": "Other"}).json()
    assert client.get(f"/api/incidents/{other['id']}/recordings/{recording['id']}/media").status_code == 404


@pytest.mark.parametrize("filename,content", [("file.txt", b"text"), ("empty.mp4", b""), ("bad.mp4", b"not video"), ("bad.mp4", b"\x00\x00\x00\x18ftypisom" + b"broken" * 50)])
def test_invalid_uploads_leave_no_storage(client, incident, settings, filename, content):
    assert upload(client, incident["id"], content, filename=filename).status_code == 422
    assert list(settings.media_root.iterdir()) == []
    assert client.get(f"/api/incidents/{incident['id']}").json()["recordings"] == []


def test_truncated_media_rejected(client, incident, media_fixture, settings):
    response = upload(client, incident["id"], media_fixture[: len(media_fixture) // 2])
    assert response.status_code == 422, response.text
    assert list(settings.media_root.iterdir()) == []


def test_validation_deadline_covers_probe_and_decode(client, incident, media_fixture, settings, monkeypatch):
    from bop_api import media
    times = iter([0.0, settings.media_validation_timeout_seconds + 1])
    monkeypatch.setattr(media, "monotonic", lambda: next(times))
    response = upload(client, incident["id"], media_fixture)
    assert response.status_code == 422
    assert "timed out" in response.json()["detail"]
    assert list(settings.media_root.iterdir()) == []


def test_unsupported_video_codec_rejected(client, incident, settings, tmp_path):
    path = tmp_path / "unsupported.mp4"
    subprocess.run([settings.ffmpeg_binary, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=s=160x120:r=10", "-t", "1", "-c:v", "mpeg4", str(path)], check=True, timeout=30)
    response = upload(client, incident["id"], path.read_bytes())
    assert response.status_code == 422
    assert "H.264" in response.json()["detail"]
    assert list(settings.media_root.iterdir()) == []


def test_media_tool_failure_is_actionable_and_cleans_storage(settings, clock, incident, media_fixture):
    unavailable = replace(settings, ffmpeg_binary="/missing/ffmpeg")
    with TestClient(create_app(unavailable, clock)) as client:
        response = upload(client, incident["id"], media_fixture)
        assert response.status_code == 503
        assert "FFmpeg" in response.json()["detail"]
    assert list(settings.media_root.iterdir()) == []


def test_exact_upload_limit_and_oversized_body(app, settings, clock, incident, media_fixture):
    limited = replace(settings, max_upload_bytes=len(media_fixture))
    with TestClient(create_app(limited, clock)) as client:
        assert upload(client, incident["id"], media_fixture).status_code == 201
        assert upload(client, incident["id"], media_fixture + b"x").status_code == 413
        assert upload(client, incident["id"], b"x" * (len(media_fixture) + 66_000)).status_code == 413
        body = b"x" * (len(media_fixture) + 66_000)
        response = client.post(f"/api/incidents/{incident['id']}/recordings", content=iter([body]), headers={"Content-Type": "multipart/form-data; boundary=x"})
        assert response.status_code == 413
    assert len(list(settings.media_root.glob("*.mp4"))) == 1
    assert list(settings.media_root.glob("*.upload")) == []


@pytest.mark.parametrize("offset", ["-1", "NaN", "Infinity", "86401"])
def test_upload_offset_validation(client, incident, media_fixture, offset):
    assert upload(client, incident["id"], media_fixture, offset=offset).status_code == 422


def test_alignment_validation_and_duration(client, prepared):
    recording = prepared["recordings"][1]
    path = f"/api/incidents/{prepared['id']}/recordings/{recording['id']}"
    response = client.patch(path, json={"camera_label": " Second feed ", "start_offset_seconds": 7.25})
    assert response.status_code == 200
    assert response.json()["camera_label"] == "Second feed"
    updated = client.get(f"/api/incidents/{prepared['id']}").json()
    assert updated["playback"]["duration_seconds"] == pytest.approx(7.25 + recording["duration_seconds"])
    assert updated["playback"]["revision"] == prepared["playback"]["revision"] + 1
    for payload in [{}, {"camera_label": " "}, {"camera_label": None}, {"start_offset_seconds": -0.5}, {"start_offset_seconds": 86_401}]:
        assert client.patch(path, json=payload).status_code == 422
    for number in ["NaN", "Infinity", "-Infinity"]:
        assert client.patch(path, content='{"start_offset_seconds":' + number + '}', headers={"content-type": "application/json"}).status_code == 422


def test_authoritative_clock_pause_and_restart(client, prepared, clock, app, media_fixture):
    incident_id = prepared["id"]
    start = control(client, incident_id, "play", prepared["playback"]["revision"])
    assert start.status_code == 200
    state = start.json()
    clock.advance(1.25)
    live = client.get(f"/api/incidents/{incident_id}/playback").json()
    assert live["position_seconds"] == 1.25
    assert live["state"] == "playing"
    assert upload(client, incident_id, media_fixture).status_code == 409
    paused = control(client, incident_id, "pause", state["revision"]).json()
    assert paused["position_seconds"] == 1.25
    clock.advance(100)
    assert client.get(f"/api/incidents/{incident_id}/playback").json()["position_seconds"] == 1.25
    recording = prepared["recordings"][0]
    assert client.patch(f"/api/incidents/{incident_id}/recordings/{recording['id']}", json={"start_offset_seconds": 1}).status_code == 409
    resumed = control(client, incident_id, "play", paused["revision"]).json()
    clock.advance(1)
    assert client.get(f"/api/incidents/{incident_id}/playback").json()["position_seconds"] == 2.25
    restarted = control(client, incident_id, "restart", resumed["revision"]).json()
    assert restarted["run_id"] != state["run_id"]
    assert restarted["position_seconds"] == 0
    assert restarted["state"] == "paused"
    assert restarted["revision"] == resumed["revision"] + 1
    assert control(client, incident_id, "play", resumed["revision"]).status_code == 409
    with app.state.sessions() as session:
        assert len(list(session.scalars(select(PlaybackRun)))) == 2


def test_seek_preserves_run_and_clamps(client, prepared, clock):
    incident_id = prepared["id"]
    duration = prepared["playback"]["duration_seconds"]
    started = control(client, incident_id, "play", prepared["playback"]["revision"]).json()
    clock.advance(1.5)
    live = client.get(f"/api/incidents/{incident_id}/playback").json()
    assert live["state"] == "playing"

    seek = client.post(f"/api/incidents/{incident_id}/playback",
                       json={"action": "seek", "expected_revision": started["revision"], "position_seconds": 0.5}).json()
    assert seek["run_id"] == started["run_id"]
    assert seek["state"] == "paused"
    assert seek["position_seconds"] == pytest.approx(0.5)

    # Clock does not advance while paused after a seek.
    clock.advance(10)
    assert client.get(f"/api/incidents/{incident_id}/playback").json()["position_seconds"] == pytest.approx(0.5)

    # Seeking past the duration clamps and ends playback.
    ended = client.post(f"/api/incidents/{incident_id}/playback",
                        json={"action": "seek", "expected_revision": seek["revision"], "position_seconds": duration + 100}).json()
    assert ended["state"] == "ended"
    assert ended["position_seconds"] == pytest.approx(duration)
    assert ended["run_id"] == started["run_id"]


@pytest.mark.parametrize("payload,status", [
    ({"action": "seek", "expected_revision": 0}, 422),
    ({"action": "play", "expected_revision": 0, "position_seconds": 1.0}, 422),
    ({"action": "pause", "expected_revision": 0, "position_seconds": 1.0}, 422),
    ({"action": "restart", "expected_revision": 0, "position_seconds": 1.0}, 422),
    ({"action": "seek", "expected_revision": 0, "position_seconds": -1}, 422),
    ({"action": "seek", "expected_revision": 0, "position_seconds": 86_401}, 422),
])
def test_playback_command_validation(client, prepared, payload, status):
    response = client.post(f"/api/incidents/{prepared['id']}/playback", json=payload)
    assert response.status_code == status


def test_end_clamps_to_latest_offset_plus_duration(client, prepared, clock):
    started = control(client, prepared["id"], "play", prepared["playback"]["revision"]).json()
    clock.advance(100)
    ended = client.get(f"/api/incidents/{prepared['id']}/playback").json()
    assert ended["state"] == "ended"
    assert ended["position_seconds"] == prepared["playback"]["duration_seconds"]
    assert control(client, prepared["id"], "play", started["revision"]).status_code == 409


def test_state_and_media_survive_new_app(client, prepared, settings, clock):
    started = control(client, prepared["id"], "play", prepared["playback"]["revision"]).json()
    clock.advance(0.75)
    with TestClient(create_app(settings, clock)) as restarted:
        detail = restarted.get(f"/api/incidents/{prepared['id']}").json()
        assert detail["recordings"] == prepared["recordings"]
        assert detail["playback"]["run_id"] == started["run_id"]
        assert detail["playback"]["position_seconds"] == 0.75
        assert restarted.get(detail["recordings"][0]["media_url"]).status_code == 200


def test_concurrent_controls_only_one_revision_wins(client, prepared):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: control(client, prepared["id"], "play", prepared["playback"]["revision"]), range(2)))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert client.get(f"/api/incidents/{prepared['id']}/playback").json()["revision"] == prepared["playback"]["revision"] + 1


def test_single_recording_can_play(client, incident, media_fixture):
    response = upload(client, incident["id"], media_fixture)
    assert response.status_code == 201
    detail = client.get(f"/api/incidents/{incident['id']}").json()
    started = control(client, incident["id"], "play", detail["playback"]["revision"])
    assert started.status_code == 200
    assert started.json()["state"] == "playing"


def test_more_than_three_recordings_can_be_uploaded(client, incident, media_fixture):
    for index in range(4):
        response = upload(client, incident["id"], media_fixture, f"Camera {index}")
        assert response.status_code == 201, response.text
    assert len(client.get(f"/api/incidents/{incident['id']}").json()["recordings"]) == 4


def test_concurrent_uploads_all_persist(client, prepared, media_fixture, settings):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: upload(client, prepared["id"], media_fixture, f"Camera {n}"), range(2)))
    assert sorted(response.status_code for response in results) == [201, 201]
    assert len(client.get(f"/api/incidents/{prepared['id']}").json()["recordings"]) == 4
    assert len(list(settings.media_root.glob("*.mp4"))) == 4
    assert list(settings.media_root.glob("*.upload")) == []


def test_playback_started_during_upload_rechecks_fresh_state(client, prepared, media_fixture, settings, monkeypatch):
    from bop_api import main
    actual_stage = main.stage_upload

    def stage_then_start(*args, **kwargs):
        result = actual_stage(*args, **kwargs)
        response = control(client, prepared["id"], "play", prepared["playback"]["revision"])
        assert response.status_code == 200
        return result

    monkeypatch.setattr(main, "stage_upload", stage_then_start)
    response = upload(client, prepared["id"], media_fixture)
    assert response.status_code == 409
    assert len(list(settings.media_root.glob("*.mp4"))) == 2
    assert list(settings.media_root.glob("*.upload")) == []


def test_commit_failure_removes_staged_file(client, incident, app, media_fixture, settings):
    def fail_media_commit(session):
        if any(isinstance(item, Recording) for item in session.identity_map.values()):
            raise SQLAlchemyError("Injected unavailable database")
    session_type = app.state.sessions.class_
    event.listen(session_type, "before_commit", fail_media_commit)
    try:
        response = upload(client, incident["id"], media_fixture)
        assert response.status_code == 503, response.text
    finally:
        event.remove(session_type, "before_commit", fail_media_commit)
    assert list(settings.media_root.iterdir()) == []
    assert client.get(f"/api/incidents/{incident['id']}").json()["recordings"] == []


def test_delete_incident_removes_records_media_and_related_runs(client, prepared, app, settings, clock):
    incident_id = prepared["id"]
    run_id = prepared["playback"]["run_id"]
    recording_id = prepared["recordings"][0]["id"]
    assert list(settings.media_root.glob("*.mp4"))
    with app.state.sessions() as session:
        session.add(TranscriptSegment(
            id="seg-delete", run_id=run_id, recording_id=recording_id,
            local_start_seconds=0, local_end_seconds=1, incident_start_seconds=0,
            incident_end_seconds=1, status="completed", text="source", created_at=clock(),
        ))
        session.add(EventHistory(run_id=run_id, events_json="[]"))
        session.add(SituationReportVersion(
            id="sitrep-delete", run_id=run_id, input_hash="a" * 64, known_through=1,
            created_at=clock(), status="completed", payload_json="{}",
        ))
        session.add(KnowledgeVersion(
            id="know-delete", run_id=run_id, input_hash="b" * 64, status="completed",
            model="test", created_at=clock(),
        ))
        session.flush()
        session.add(KnowledgeObservation(
            id="obs-delete", version_id="know-delete", segment_id="seg-delete",
            text="observed", attribution="Camera A",
        ))
        session.add(KnowledgeItem(
            id="item-delete", version_id="know-delete", item_key="n1", kind="claim",
            label="Claim", description="Claim", uncertainty="",
        ))
        session.flush()
        session.add(KnowledgeSupport(item_id="item-delete", observation_id="obs-delete"))
        session.commit()
    current = client.get(f"/api/incidents/{incident_id}").json()
    assert control(client, incident_id, "restart", current["playback"]["revision"]).status_code == 200
    other = client.post("/api/incidents", json={"title": "Keep this incident"}).json()
    response = client.delete(f"/api/incidents/{incident_id}")
    assert response.status_code == 204, response.text
    assert client.get(f"/api/incidents/{incident_id}").status_code == 404
    assert client.delete(f"/api/incidents/{incident_id}").status_code == 404
    listed = client.get("/api/incidents").json()
    assert incident_id not in [entry["id"] for entry in listed]
    assert other["id"] in [entry["id"] for entry in listed]
    assert client.get(f"/api/incidents/{other['id']}").status_code == 200
    assert list(settings.media_root.glob("*.mp4")) == []
    with app.state.sessions() as session:
        assert session.get(PlaybackRun, run_id) is None
        assert session.get(Recording, recording_id) is None
        assert session.get(TranscriptSegment, "seg-delete") is None
        assert session.get(EventHistory, run_id) is None
        assert session.get(SituationReportVersion, "sitrep-delete") is None
        assert session.get(KnowledgeVersion, "know-delete") is None


def test_health_requires_database_media_tools_and_storage(client, app, settings, clock):
    assert client.get("/api/health").status_code == 200
    with TestClient(create_app(replace(settings, ffmpeg_binary="/missing/ffmpeg"), clock)) as unhealthy:
        response = unhealthy.get("/api/health")
        assert response.status_code == 503
        assert response.json()["media_tools"] is False
