"""Provider interface and the xAI Grok batch STT adapter.

Keeping the provider behind a small protocol lets tests inject a fake and
lets us swap models without changing the segment/release pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Protocol
from urllib import error as urllib_error, request as urllib_request
import uuid


class TranscriberError(RuntimeError):
    """Raised when a provider call fails and the segment should be re-run or failed."""


@dataclass
class SpeakerTurn:
    """One contiguous run of speech attributed to a single anonymous speaker."""

    speaker: int
    text: str
    start: float
    end: float


@dataclass
class TranscriberResult:
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)
    turns: list[SpeakerTurn] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> TranscriberResult:  # pragma: no cover - protocol
        ...


class NullTranscriber:
    """Fallback used when no provider is configured.

    Raises on use so a misconfigured deployment surfaces the missing key
    instead of quietly recording empty transcripts.
    """

    def transcribe(self, audio_path: Path) -> TranscriberResult:
        raise TranscriberError(
            "Transcription is not configured. Set XAI_API_KEY to enable Grok STT."
        )


def group_speaker_turns(words: list[dict[str, Any]]) -> list[SpeakerTurn]:
    """Collapse consecutive words that share a speaker into turns.

    Speaker ids are anonymous and segment-local (Grok does not persist identity
    across requests). Words without a speaker field are treated as speaker 0.
    """
    turns: list[SpeakerTurn] = []
    current_speaker: int | None = None
    buffer: list[str] = []
    start = 0.0
    end = 0.0
    for word in words:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        raw_speaker = word.get("speaker")
        try:
            speaker = int(raw_speaker) if raw_speaker is not None else 0
        except (TypeError, ValueError):
            speaker = 0
        word_start = float(word.get("start", end))
        word_end = float(word.get("end", word_start))
        if current_speaker is None:
            current_speaker = speaker
            buffer = [text]
            start = word_start
            end = word_end
            continue
        if speaker == current_speaker:
            buffer.append(text)
            end = word_end
            continue
        turns.append(
            SpeakerTurn(
                speaker=current_speaker,
                text=_join_words(buffer),
                start=start,
                end=end,
            )
        )
        current_speaker = speaker
        buffer = [text]
        start = word_start
        end = word_end
    if current_speaker is not None and buffer:
        turns.append(
            SpeakerTurn(
                speaker=current_speaker,
                text=_join_words(buffer),
                start=start,
                end=end,
            )
        )
    return turns


def format_diarized_text(turns: list[SpeakerTurn], plain_text: str) -> str:
    """Render anonymous speaker turns for storage and plain-text clients.

    Labels are 1-based for display (Speaker 1, Speaker 2) and do not claim
    officer/witness identity.
    """
    if not turns:
        return plain_text
    if len(turns) == 1 and turns[0].speaker == 0:
        # Single anonymous stream — keep the plain transcript without a label.
        return turns[0].text or plain_text
    return "\n".join(f"Speaker {turn.speaker + 1}: {turn.text}" for turn in turns if turn.text)


def _join_words(parts: list[str]) -> str:
    text = " ".join(parts)
    # Light cleanup for punctuation glued by the STT word stream.
    for mark in (".", ",", "!", "?", ";", ":"):
        text = text.replace(f" {mark}", mark)
    return text.strip()


class GrokTranscriber:
    """xAI Grok Speech-to-Text (batch) provider with speaker diarization.

    See https://docs.x.ai/developers/model-capabilities/audio/speech-to-text
    """

    def __init__(
        self,
        api_key: str,
        *,
        url: str = "https://api.x.ai/v1/stt",
        language: str = "en",
        timeout_seconds: float = 90,
        diarize: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("GrokTranscriber requires a non-empty XAI_API_KEY")
        self._api_key = api_key
        self._url = url
        self._language = language
        self._timeout = timeout_seconds
        self._diarize = diarize

    def transcribe(self, audio_path: Path) -> TranscriberResult:
        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as exc:
            raise TranscriberError(f"Could not read extracted audio: {exc}") from exc

        boundary = f"----bopstt{uuid.uuid4().hex}"
        fields = [
            ("language", self._language),
            ("format", "true"),
        ]
        if self._diarize:
            fields.append(("diarize", "true"))
        body = _multipart_body(
            boundary,
            fields=fields,
            files=[("file", audio_path.name, "audio/wav", audio_bytes)],
        )
        request = urllib_request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        try:
            with urllib_request.urlopen(request, timeout=self._timeout) as response:
                payload_bytes = response.read()
                status = response.status
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise TranscriberError(
                f"Grok STT returned HTTP {exc.code}: {detail[:200]}"
            ) from exc
        except urllib_error.URLError as exc:
            raise TranscriberError(f"Grok STT request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TranscriberError("Grok STT request timed out.") from exc

        if status >= 400:
            raise TranscriberError(f"Grok STT returned HTTP {status}")
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TranscriberError("Grok STT returned an unreadable response body.") from exc

        plain = (payload.get("text") or "").strip()
        raw_words = payload.get("words") or []
        words: list[dict[str, Any]] = []
        for word in raw_words:
            if not isinstance(word, dict):
                continue
            entry: dict[str, Any] = {
                "start": float(word.get("start", 0)),
                "end": float(word.get("end", 0)),
                "text": str(word.get("text", "")),
            }
            if "speaker" in word and word["speaker"] is not None:
                try:
                    entry["speaker"] = int(word["speaker"])
                except (TypeError, ValueError):
                    pass
            words.append(entry)
        turns = group_speaker_turns(words) if self._diarize else []
        text = format_diarized_text(turns, plain) if turns else plain
        return TranscriberResult(text=text, words=words, turns=turns)


def _multipart_body(
    boundary: str,
    *,
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, str, bytes]],
) -> bytes:
    """Build a multipart/form-data body. ``file`` fields go last per xAI docs."""
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")
    for name, filename, content_type, content in files:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)
