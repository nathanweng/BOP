import json
import math
import os
from pathlib import Path
import subprocess
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from .config import Settings


def media_path(settings: Settings, key: str) -> Path:
    # Storage keys are generated internally and never derived from a filename.
    if len(key) != 36 or not key.endswith(".mp4") or any(c not in "0123456789abcdef" for c in key[:-4]):
        raise HTTPException(500, "The media reference is invalid")
    return settings.media_root / key


def validate_media(path: Path, settings: Settings) -> float:
    deadline = monotonic() + settings.media_validation_timeout_seconds
    try:
        with path.open("rb") as source:
            header = source.read(64)
        # ISO base media ftyp, excluding QuickTime-only MOV containers.
        if len(header) < 12 or header[4:8] != b"ftyp" or header[8:12] == b"qt  ":
            raise HTTPException(422, "The file must use an MP4 container.")
        result = subprocess.run(
            [settings.ffprobe_binary, "-v", "error", "-protocol_whitelist", "file,pipe",
             "-show_entries", "format=duration:stream=codec_type,codec_name,pix_fmt", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=settings.media_validation_timeout_seconds,
        )
        if result.returncode:
            raise HTTPException(422, "This file cannot be read as an MP4. Export it as H.264 MP4 and retry.")
        probe = json.loads(result.stdout)
        streams = probe.get("streams", [])
        video = [s for s in streams if s.get("codec_type") == "video"]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        duration = float(probe.get("format", {}).get("duration", 0))
        if len(video) != 1 or video[0].get("codec_name") != "h264":
            raise HTTPException(422, "Use an MP4 with one H.264 video track.")
        if video[0].get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
            raise HTTPException(422, "Use 8-bit H.264 video with 4:2:0 color for browser playback.")
        if len(audio) > 1 or any(s.get("codec_name") != "aac" for s in audio):
            raise HTTPException(422, "Use at most one AAC audio track (or no audio).")
        if not math.isfinite(duration) or duration <= 0:
            raise HTTPException(422, "The recording must have a positive, readable duration.")
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(settings.ffmpeg_binary, settings.media_validation_timeout_seconds)
        decoded = subprocess.run(
            [settings.ffmpeg_binary, "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode", "-protocol_whitelist", "file,pipe", "-i", str(path),
             "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=remaining,
        )
        if decoded.returncode:
            raise HTTPException(422, "The recording contains unreadable media. Re-export the source as H.264 MP4.")
        return duration
    except FileNotFoundError as exc:
        raise HTTPException(503, "Media validation is unavailable: FFmpeg and FFprobe are required.") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(422, "Media validation timed out. Try a shorter recording.") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, "The recording metadata is unreadable.") from exc


def stage_upload(upload: UploadFile, settings: Settings) -> tuple[Path, int, float]:
    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(".mp4") or len(filename) > 255:
        raise HTTPException(422, "Choose an MP4 file with a filename of at most 255 characters.")
    temporary = settings.media_root / f"{uuid4().hex}.upload"
    size = 0
    try:
        with temporary.open("xb") as output:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, f"The recording exceeds the {settings.max_upload_bytes}-byte upload limit.")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size == 0:
            raise HTTPException(422, "The recording is empty.")
        return temporary, size, validate_media(temporary, settings)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        upload.file.close()
