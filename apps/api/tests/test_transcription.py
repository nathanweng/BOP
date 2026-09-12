"""Cutoff-gated live transcription tests.

Covers the pure segment-release math, run isolation across restarts, and the
fake-provider path end-to-end without any network call.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import pytest

from bop_api.main import create_app
from bop_api.transcription import (
    NullTranscriber,
    TranscriberError,
    TranscriberResult,
    format_diarized_text,
    group_speaker_turns,
)
from bop_api.transcription.eligibility import eligible_windows
from conftest import upload


def test_group_speaker_turns_splits_consecutive_speakers():
    turns = group_speaker_turns([
        {"text": "She", "start": 0.0, "end": 0.2, "speaker": 0},
        {"text": "hit", "start": 0.2, "end": 0.4, "speaker": 0},
        {"text": "us.", "start": 0.4, "end": 0.6, "speaker": 0},
        {"text": "Is", "start": 0.7, "end": 0.8, "speaker": 1},
        {"text": "she", "start": 0.8, "end": 1.0, "speaker": 1},
        {"text": "okay?", "start": 1.0, "end": 1.2, "speaker": 1},
        {"text": "I'm", "start": 1.3, "end": 1.5, "speaker": 0},
        {"text": "trying.", "start": 1.5, "end": 1.8, "speaker": 0},
    ])
    assert [(t.speaker, t.text) for t in turns] == [
        (0, "She hit us."),
        (1, "Is she okay?"),
        (0, "I'm trying."),
    ]
    assert format_diarized_text(turns, "plain") == (
        "Speaker 1: She hit us.\nSpeaker 2: Is she okay?\nSpeaker 1: I'm trying."
    )


def test_format_diarized_text_keeps_single_speaker_plain():
    turns = group_speaker_turns([
        {"text": "Hello", "start": 0.0, "end": 0.3, "speaker": 0},
        {"text": "there.", "start": 0.3, "end": 0.6, "speaker": 0},
    ])
    assert format_diarized_text(turns, "Hello there.") == "Hello there."


def _windows(cutoff, offset=0.0, duration=25.0, segment=10.0):
    return [
        (round(w.local_start, 3), round(w.local_end, 3), round(w.incident_start, 3), round(w.incident_end, 3))
        for w in eligible_windows(
            incident_position_seconds=cutoff,
            start_offset_seconds=offset,
            duration_seconds=duration,
            segment_seconds=segment,
        )
    ]


def test_eligibility_never_returns_windows_beyond_cutoff():
    assert _windows(0) == []
    assert _windows(4.5) == []
    assert _windows(10.0) == [(0, 10, 0, 10)]
    assert _windows(19.9) == [(0, 10, 0, 10)]
    assert _windows(20.0) == [(0, 10, 0, 10), (10, 20, 10, 20)]


def test_eligibility_offsets_and_terminal_partial_window():
    assert _windows(5, offset=6, duration=25) == []
    assert _windows(16, offset=6, duration=25) == [(0, 10, 6, 16)]
    assert _windows(35, offset=6, duration=25) == [
        (0, 10, 6, 16), (10, 20, 16, 26), (20, 25, 26, 31),
    ]


def test_eligibility_short_recording_releases_full_only_after_end():
    assert _windows(2.5, duration=3.0, segment=10) == []
    assert _windows(3.0, duration=3.0, segment=10) == [(0, 3, 0, 3)]
    assert _windows(50.0, duration=3.0, segment=10) == [(0, 3, 0, 3)]


class FakeTranscriber:
    """In-process transcriber that returns deterministic text per audio file."""

    def __init__(self):
        self.calls: list[Path] = []
        self.lock = threading.Lock()
        self.response = TranscriberResult(text="Fake transcript.", words=[])

    def transcribe(self, audio_path: Path) -> TranscriberResult:
        with self.lock:
            self.calls.append(audio_path)
            assert audio_path.exists(), "Extracted audio must exist for the provider call"
            assert audio_path.stat().st_size > 44, "Provider received an empty audio file"
        return self.response


class DiarizedFakeTranscriber:
    def transcribe(self, audio_path: Path) -> TranscriberResult:
        words = [
            {"text": "She", "start": 0.0, "end": 0.2, "speaker": 0},
            {"text": "hit", "start": 0.2, "end": 0.4, "speaker": 0},
            {"text": "us.", "start": 0.4, "end": 0.6, "speaker": 0},
            {"text": "Okay?", "start": 0.7, "end": 1.0, "speaker": 1},
        ]
        turns = group_speaker_turns(words)
        return TranscriberResult(
            text=format_diarized_text(turns, "She hit us. Okay?"),
            words=words,
            turns=turns,
        )


class RaisingTranscriber:
    def transcribe(self, audio_path: Path) -> TranscriberResult:
        raise TranscriberError("provider unavailable")


@pytest.fixture
def transcribing_app(settings, clock, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    transcriber = FakeTranscriber()
    app = create_app(settings, clock, transcriber=transcriber)
    return app, transcriber


@pytest.fixture
def transcribing_client(transcribing_app):
    app, transcriber = transcribing_app
    with TestClient(app) as client:
        yield client, app, transcriber


def _prepared(client, media_fixture, offsets=(0, 0)):
    incident = client.post("/api/incidents", json={"title": "Transcript incident"}).json()
    labels = ["Camera A", "Camera B", "Camera C"]
    for label, offset in zip(labels, offsets):
        result = upload(client, incident["id"], media_fixture, label, offset)
        assert result.status_code == 201, result.text
    return client.get(f"/api/incidents/{incident['id']}").json()


def test_playback_releases_and_transcribes_only_reached_windows(transcribing_client, media_fixture, clock):
    client, app, transcriber = transcribing_client
    prepared = _prepared(client, media_fixture, offsets=(0, 0))
    incident_id = prepared["id"]
    play = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
    )
    assert play.status_code == 200

    # Nothing eligible: cutoff has not reached one full segment.
    clock.advance(0.5)
    client.get(f"/api/incidents/{incident_id}/playback")
    transcripts = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    for feed in transcripts["recordings"]:
        assert feed["segments"] == []
    assert transcripts["transcription_configured"] is False

    # After the recording ends, the whole 3s window becomes releasable
    # because segment_seconds (10) exceeds the recording's duration.
    clock.advance(4.0)
    client.get(f"/api/incidents/{incident_id}/playback")
    app.state.transcription.process_pending()

    transcripts = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    assert len(transcripts["recordings"]) == 2
    for feed in transcripts["recordings"]:
        assert len(feed["segments"]) == 1
        segment = feed["segments"][0]
        assert segment["status"] == "completed"
        assert segment["text"] == "Fake transcript."
        assert segment["local_start_seconds"] == 0
        assert 2.5 < segment["local_end_seconds"] <= 3.2
        assert feed["latest_analyzed_time_seconds"] == segment["incident_end_seconds"]
    assert len(transcriber.calls) == 2


def test_diarized_transcript_exposes_anonymous_speaker_turns(settings, clock, media_fixture, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    app = create_app(settings, clock, transcriber=DiarizedFakeTranscriber())
    with TestClient(app) as client:
        prepared = _prepared(client, media_fixture, offsets=(0, 0))
        play = client.post(
            f"/api/incidents/{prepared['id']}/playback",
            json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
        )
        assert play.status_code == 200
        clock.advance(4)
        client.get(f"/api/incidents/{prepared['id']}/playback")
        app.state.transcription.process_pending()
        transcripts = client.get(f"/api/incidents/{prepared['id']}/transcripts").json()
        for feed in transcripts["recordings"]:
            assert len(feed["segments"]) == 1
            segment = feed["segments"][0]
            assert segment["status"] == "completed"
            assert segment["text"] == "Speaker 1: She hit us.\nSpeaker 2: Okay?"
            assert segment["turns"] == [
                {
                    "speaker": 1,
                    "label": "Speaker 1",
                    "text": "She hit us.",
                    "local_start_seconds": 0.0,
                    "local_end_seconds": 0.6,
                },
                {
                    "speaker": 2,
                    "label": "Speaker 2",
                    "text": "Okay?",
                    "local_start_seconds": 0.7,
                    "local_end_seconds": 1.0,
                },
            ]


def test_restart_isolates_transcripts_between_runs(transcribing_client, media_fixture, clock):
    client, app, _ = transcribing_client
    prepared = _prepared(client, media_fixture, offsets=(0, 0))
    incident_id = prepared["id"]
    first = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
    ).json()
    clock.advance(4.0)
    client.get(f"/api/incidents/{incident_id}/playback")
    app.state.transcription.process_pending()
    first_transcripts = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    assert all(len(feed["segments"]) == 1 for feed in first_transcripts["recordings"])

    restart = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "restart", "expected_revision": first["revision"]},
    ).json()
    assert restart["run_id"] != first["run_id"]

    fresh = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    assert fresh["run_id"] == restart["run_id"]
    for feed in fresh["recordings"]:
        assert feed["segments"] == []
        assert feed["latest_analyzed_time_seconds"] is None


def test_seek_preserves_transcripts_within_run(transcribing_client, media_fixture, clock):
    client, app, transcriber = transcribing_client
    prepared = _prepared(client, media_fixture, offsets=(0, 0))
    incident_id = prepared["id"]
    started = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
    ).json()
    clock.advance(4.0)
    client.get(f"/api/incidents/{incident_id}/playback")
    app.state.transcription.process_pending()
    before = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    assert all(len(feed["segments"]) == 1 for feed in before["recordings"])
    calls_before = len(transcriber.calls)

    seek = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "seek", "expected_revision": started["revision"], "position_seconds": 0},
    ).json()
    assert seek["run_id"] == started["run_id"]
    assert seek["state"] == "paused"
    assert seek["position_seconds"] == 0

    after = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    assert after["run_id"] == started["run_id"]
    for feed_before, feed_after in zip(before["recordings"], after["recordings"]):
        assert feed_after["segments"] == feed_before["segments"]
        assert feed_after["latest_analyzed_time_seconds"] == feed_before["latest_analyzed_time_seconds"]

    # Re-enqueueing after a seek must not call the transcriber again.
    app.state.transcription.process_pending()
    assert len(transcriber.calls) == calls_before


def test_paused_playback_freezes_release(transcribing_client, media_fixture, clock):
    client, app, _ = transcribing_client
    prepared = _prepared(client, media_fixture, offsets=(0, 0))
    incident_id = prepared["id"]
    play = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
    ).json()
    clock.advance(1.0)
    paused = client.post(
        f"/api/incidents/{incident_id}/playback",
        json={"action": "pause", "expected_revision": play["revision"]},
    ).json()
    assert paused["state"] == "paused"
    # Wall-clock time advances but the frozen incident clock does not release new work.
    clock.advance(30)
    client.get(f"/api/incidents/{incident_id}/playback")
    app.state.transcription.process_pending()
    transcripts = client.get(f"/api/incidents/{incident_id}/transcripts").json()
    for feed in transcripts["recordings"]:
        assert feed["segments"] == []


def test_silent_recording_produces_empty_status(transcribing_client, settings, clock, tmp_path):
    client, app, transcriber = transcribing_client
    # Build a silent H.264 MP4 (no audio track) using FFmpeg directly.
    silent = tmp_path / "silent.mp4"
    import subprocess

    subprocess.run(
        [settings.ffmpeg_binary, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
         "color=c=black:s=160x120:r=10", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(silent)],
        check=True, timeout=30,
    )
    incident = client.post("/api/incidents", json={"title": "Silent"}).json()
    for label in ("Camera A", "Camera B"):
        response = upload(client, incident["id"], silent.read_bytes(), label)
        assert response.status_code == 201, response.text
    prepared = client.get(f"/api/incidents/{incident['id']}").json()
    play = client.post(
        f"/api/incidents/{prepared['id']}/playback",
        json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
    )
    assert play.status_code == 200
    clock.advance(4)
    client.get(f"/api/incidents/{prepared['id']}/playback")
    app.state.transcription.process_pending()

    transcripts = client.get(f"/api/incidents/{prepared['id']}/transcripts").json()
    for feed in transcripts["recordings"]:
        assert len(feed["segments"]) == 1
        assert feed["segments"][0]["status"] == "empty"
        assert feed["segments"][0]["text"] is None
    assert transcriber.calls == []


def test_provider_error_records_failed_but_does_not_stop_playback(settings, clock, media_fixture, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    app = create_app(settings, clock, transcriber=RaisingTranscriber())
    with TestClient(app) as client:
        prepared = _prepared(client, media_fixture, offsets=(0, 0))
        play = client.post(
            f"/api/incidents/{prepared['id']}/playback",
            json={"action": "play", "expected_revision": prepared["playback"]["revision"]},
        )
        assert play.status_code == 200
        clock.advance(4)
        client.get(f"/api/incidents/{prepared['id']}/playback")
        app.state.transcription.process_pending()
        transcripts = client.get(f"/api/incidents/{prepared['id']}/transcripts").json()
        failures = [seg for feed in transcripts["recordings"] for seg in feed["segments"]]
        assert failures
        for segment in failures:
            assert segment["status"] == "failed"
            assert "provider unavailable" in (segment["error"] or "")
        # Failed segments must never advance the analyzed cutoff.
        for feed in transcripts["recordings"]:
            assert feed["latest_analyzed_time_seconds"] is None


def test_health_reports_transcription_configured(settings, clock, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    unconfigured = create_app(settings, clock)
    with TestClient(unconfigured) as client:
        body = client.get("/api/health").json()
        assert body["transcription_configured"] is False
        assert body["events_configured"] is False

    configured = create_app(replace(settings, xai_api_key="test-key"), clock, transcriber=FakeTranscriber())
    with TestClient(configured) as client:
        body = client.get("/api/health").json()
        assert body["transcription_configured"] is True


def test_null_transcriber_surfaces_configuration_error():
    with pytest.raises(TranscriberError):
        NullTranscriber().transcribe(Path("/tmp/does-not-matter"))


@pytest.mark.parametrize('speed', [1, 2, 4])
def test_accelerated_release_keeps_original_source_times(transcribing_client, media_fixture, clock, speed):
    client, app, _ = transcribing_client
    prepared = _prepared(client, media_fixture, offsets=(2,))
    iid = prepared['id']
    faster = client.post(f'/api/incidents/{iid}/playback', json={
        'action': 'set_speed', 'speed': speed, 'expected_revision': prepared['playback']['revision'],
    }).json()
    client.post(f'/api/incidents/{iid}/playback', json={
        'action': 'play', 'expected_revision': faster['revision'],
    })
    clock.advance(4 / speed)
    client.get(f'/api/incidents/{iid}/playback')
    assert app.state.transcription.process_pending() == 0  # Window still unreleased.
    clock.advance(2 / speed)
    client.get(f'/api/incidents/{iid}/playback')
    assert app.state.transcription.process_pending() == 1
    feed = client.get(f'/api/incidents/{iid}/transcripts').json()['recordings'][0]
    assert feed['segments'][0]['incident_start_seconds'] == 2
    assert feed['segments'][0]['local_start_seconds'] == 0
    assert feed['segments'][0]['incident_end_seconds'] == pytest.approx(5, abs=.2)


def test_concurrent_processing_refills_slots_and_does_not_skip_analysis_gaps(app, incident, clock, monkeypatch):
    from bop_api.models import Recording, TranscriptSegment
    service = app.state.transcription
    service._settings = replace(service._settings, transcription_concurrency=2)
    rid = incident['playback']['run_id']
    with app.state.sessions() as session:
        session.add(Recording(id='parallel-cam', incident_id=incident['id'], original_filename='test.mp4',
            camera_label='A', storage_key='test.mp4', duration_seconds=40, start_offset_seconds=2,
            size_bytes=100, created_at=clock()))
        session.commit()
        for i in range(4):
            session.add(TranscriptSegment(id=f's{i}', run_id=rid, recording_id='parallel-cam',
                local_start_seconds=i * 10, local_end_seconds=(i + 1) * 10,
                incident_start_seconds=i * 10 + 2, incident_end_seconds=(i + 1) * 10 + 2,
                status='queued', created_at=clock()))
        session.commit()
    release_first, refilled = threading.Event(), threading.Event()
    calls = []
    def process(**kwargs):
        sid = kwargs['segment_id']
        calls.append(sid)
        if sid == 's0':
            assert release_first.wait(5)
        with app.state.sessions() as session:
            session.get(TranscriptSegment, sid).status = 'completed'
            session.commit()
        if sid == 's2':
            refilled.set()
    monkeypatch.setattr(service, '_process_one', process)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(service.process_pending, max_segments=3)
        try:
            assert refilled.wait(5), 'A free slot must refill while the first clip is still processing'
            assert service.latest_analyzed_by_recording(run_id=rid, recording_ids=['parallel-cam']) == {}
        finally:
            release_first.set()
        assert result.result(timeout=5) == 3
    assert set(calls) == {'s0', 's1', 's2'}
    assert service.latest_analyzed_by_recording(run_id=rid, recording_ids=['parallel-cam']) == {'parallel-cam': 32}
    with app.state.sessions() as session:
        assert session.get(TranscriptSegment, 's3').status == 'queued'
