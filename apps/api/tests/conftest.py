from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from bop_api.config import Settings
from bop_api.main import create_app


@dataclass
class Clock:
    value: datetime = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def database_url(tmp_path):
    postgres_url = os.getenv("TEST_DATABASE_URL")
    if not postgres_url:
        yield f"sqlite:///{tmp_path / 'test.db'}"
        return
    schema = f"bop_test_{uuid4().hex}"
    admin = create_engine(postgres_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(postgres_url).update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def settings(database_url, tmp_path):
    return Settings(
        database_url=database_url,
        media_root=tmp_path / "media",
        max_upload_bytes=1_000_000,
        ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
        ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
    )


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(settings, clock, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    return create_app(settings, clock)


@pytest.fixture
def client(app):
    with TestClient(app) as instance:
        yield instance


@pytest.fixture(scope="session")
def media_fixture(tmp_path_factory):
    ffmpeg = os.getenv("FFMPEG_BINARY", "ffmpeg")
    ffprobe = os.getenv("FFPROBE_BINARY", "ffprobe")
    if not shutil.which(ffmpeg) or not shutil.which(ffprobe):
        pytest.fail("FFmpeg/FFprobe are required for the media tests. Set FFMPEG_BINARY and FFPROBE_BINARY.")
    path = tmp_path_factory.mktemp("fixtures") / "camera.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=10",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(path)],
        check=True, timeout=30,
    )
    return path.read_bytes()


@pytest.fixture
def incident(client):
    response = client.post("/api/incidents", json={"title": "Training incident", "context": "Staged recordings"})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, incident_id, content, label="Camera A", offset=0, filename="camera.mp4"):
    return client.post(
        f"/api/incidents/{incident_id}/recordings",
        files={"file": (filename, content, "video/mp4")},
        data={"camera_label": label, "start_offset_seconds": str(offset)},
    )


@pytest.fixture
def prepared(client, incident, media_fixture):
    for label, offset in [("Camera A", 0), ("Camera B", 1.5)]:
        result = upload(client, incident["id"], media_fixture, label, offset)
        assert result.status_code == 201, result.text
    return client.get(f"/api/incidents/{incident['id']}").json()
