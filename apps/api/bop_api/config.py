from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://bop:bop_local@localhost:5432/bop"
    media_root: Path = Path("./media")
    max_upload_bytes: int = 536_870_912
    media_validation_timeout_seconds: float = 120
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    xai_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    event_worker_interval_seconds: float = 3.0
    xai_stt_url: str = "https://api.x.ai/v1/stt"
    xai_stt_language: str = "en"
    xai_stt_timeout_seconds: float = 90
    transcription_segment_seconds: float = 10
    transcription_worker_interval_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            media_root=Path(os.getenv("MEDIA_ROOT", "./media")).resolve(),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", "536870912")),
            media_validation_timeout_seconds=float(
                os.getenv("MEDIA_VALIDATION_TIMEOUT_SECONDS", "120")
            ),
            ffmpeg_binary=os.getenv("FFMPEG_BINARY", "ffmpeg"),
            ffprobe_binary=os.getenv("FFPROBE_BINARY", "ffprobe"),
            xai_api_key=os.getenv("XAI_API_KEY", "").strip(),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
            openrouter_model=os.getenv("OPENROUTER_MODEL", cls.openrouter_model).strip(),
            event_worker_interval_seconds=float(os.getenv("EVENT_WORKER_INTERVAL_SECONDS", "3")),
            xai_stt_url=os.getenv("XAI_STT_URL", cls.xai_stt_url),
            xai_stt_language=os.getenv("XAI_STT_LANGUAGE", cls.xai_stt_language),
            xai_stt_timeout_seconds=float(os.getenv("XAI_STT_TIMEOUT_SECONDS", "90")),
            transcription_segment_seconds=float(os.getenv("SEGMENT_SECONDS", "10")),
            transcription_worker_interval_seconds=float(
                os.getenv("TRANSCRIPTION_WORKER_INTERVAL_SECONDS", "1.0")
            ),
        )
        if settings.max_upload_bytes <= 0 or settings.media_validation_timeout_seconds <= 0:
            raise ValueError("Upload size and validation timeout must be positive")
        if settings.transcription_segment_seconds <= 0:
            raise ValueError("Transcription segment size must be positive")
        return settings
