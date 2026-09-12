"""Cutoff-gated, per-recording live transcription pipeline.

Segments are released as the shared incident clock advances. Audio for each
released window is extracted locally with FFmpeg and transcribed by an
injected provider (default: xAI Grok). Full recordings and unreleased audio
never reach the provider.
"""

from .eligibility import Window, eligible_windows, incident_window
from .service import TranscriptionService
from .transcriber import (
    GrokTranscriber,
    NullTranscriber,
    Transcriber,
    TranscriberError,
    TranscriberResult,
)

__all__ = [
    "GrokTranscriber",
    "NullTranscriber",
    "Transcriber",
    "TranscriberError",
    "TranscriberResult",
    "TranscriptionService",
    "Window",
    "eligible_windows",
    "incident_window",
]
