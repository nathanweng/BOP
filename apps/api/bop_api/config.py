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
        )
        if settings.max_upload_bytes <= 0 or settings.media_validation_timeout_seconds <= 0:
            raise ValueError("Upload size and validation timeout must be positive")
        return settings
