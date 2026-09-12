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
class TranscriberResult:
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)

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


class GrokTranscriber:
    """xAI Grok Speech-to-Text (batch) provider.

    See https://docs.x.ai/developers/model-capabilities/audio/speech-to-text
    """

    def __init__(
        self,
        api_key: str,
        *,
        url: str = "https://api.x.ai/v1/stt",
        language: str = "en",
        timeout_seconds: float = 90,
    ) -> None:
        if not api_key:
            raise ValueError("GrokTranscriber requires a non-empty XAI_API_KEY")
        self._api_key = api_key
        self._url = url
        self._language = language
        self._timeout = timeout_seconds

    def transcribe(self, audio_path: Path) -> TranscriberResult:
        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as exc:
            raise TranscriberError(f"Could not read extracted audio: {exc}") from exc

        boundary = f"----bopstt{uuid.uuid4().hex}"
        body = _multipart_body(
            boundary,
            fields=[
                ("language", self._language),
                ("format", "true"),
            ],
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

        text = (payload.get("text") or "").strip()
        raw_words = payload.get("words") or []
        words = [
            {
                "start": float(word.get("start", 0)),
                "end": float(word.get("end", 0)),
                "text": str(word.get("text", "")),
            }
            for word in raw_words
            if isinstance(word, dict)
        ]
        return TranscriberResult(text=text, words=words)


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
