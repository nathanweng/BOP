"""Extract a single audio window from a validated recording with FFmpeg.

The output is a mono 16 kHz WAV, sized for a batch STT request. The recording
is opened read-only; the input path is derived from the internal storage
key, never a user-supplied filename.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from uuid import uuid4

from ..config import Settings

WAV_HEADER_BYTES = 44


class AudioExtractionError(RuntimeError):
    pass


def _has_audio_track(settings: Settings, source: Path, timeout_seconds: float | None) -> bool:
    result = subprocess.run(
        [
            settings.ffprobe_binary, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-select_streams", "a", "-show_entries", "stream=codec_type",
            "-of", "json", str(source),
        ],
        capture_output=True, text=True, timeout=timeout_seconds,
    )
    if result.returncode:
        raise AudioExtractionError("Could not probe recording for audio streams.")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AudioExtractionError("FFprobe returned an unreadable response.") from exc
    return bool(payload.get("streams"))


def extract_audio_window(
    settings: Settings,
    source: Path,
    *,
    local_start: float,
    local_end: float,
    scratch_dir: Path,
    timeout_seconds: float | None = None,
) -> tuple[Path, bool]:
    """Extract ``[local_start, local_end)`` from ``source`` into scratch_dir.

    Returns ``(path, has_audio)``. ``has_audio`` is False when the recording
    has no audio stream at all, or when the window produced no PCM samples.
    When False, ``path`` may not exist; callers should still ``unlink`` with
    ``missing_ok=True``.
    """
    if local_end <= local_start:
        raise AudioExtractionError("Window end must be after its start")
    output = scratch_dir / f"segment-{uuid4().hex}.wav"
    try:
        if not _has_audio_track(settings, source, timeout_seconds):
            return output, False
    except FileNotFoundError as exc:
        raise AudioExtractionError(
            "FFprobe is required to detect audio for transcription."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioExtractionError("Audio probing timed out.") from exc

    duration = local_end - local_start
    command = [
        settings.ffmpeg_binary,
        "-v", "error",
        "-nostdin",
        "-y",
        "-ss", f"{local_start:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(source),
        "-vn",
        "-map", "0:a:0",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        str(output),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise AudioExtractionError(
            "FFmpeg is required to extract audio for transcription."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        output.unlink(missing_ok=True)
        raise AudioExtractionError("Audio extraction timed out.") from exc

    if result.returncode != 0:
        output.unlink(missing_ok=True)
        message = (result.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()
        detail = message[-1] if message else "FFmpeg failed while extracting audio."
        raise AudioExtractionError(f"Audio extraction failed: {detail}")

    if not output.exists():
        return output, False
    size = output.stat().st_size
    if size <= WAV_HEADER_BYTES:
        output.unlink(missing_ok=True)
        return output, False
    return output, True
