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
)
from bop_api.transcription.eligibility import eligible_windows
from conftest import upload


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

    configured = create_app(replace(settings, xai_api_key="test-key"), clock, transcriber=FakeTranscriber())
    with TestClient(configured) as client:
        body = client.get("/api/health").json()
        assert body["transcription_configured"] is True


def test_null_transcriber_surfaces_configuration_error():
    with pytest.raises(TranscriberError):
        NullTranscriber().transcribe(Path("/tmp/does-not-matter"))
