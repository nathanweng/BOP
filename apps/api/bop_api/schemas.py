from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IncidentCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    context: str = Field(default="", max_length=10_000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A title is required")
        return value


class RecordingUpdate(StrictModel):
    camera_label: str | None = Field(default=None, min_length=1, max_length=100)
    start_offset_seconds: float | None = Field(default=None, ge=0, le=86_400)

    @field_validator("camera_label")
    @classmethod
    def clean_label(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("A camera label is required")
        return value


class PlaybackCommand(StrictModel):
    action: Literal["play", "pause", "restart", "seek", "set_speed"]
    expected_revision: int = Field(ge=0, strict=True)
    position_seconds: float | None = Field(default=None, ge=0, le=86_400)
    speed: float | None = Field(default=None)

    @field_validator("speed")
    @classmethod
    def demo_speeds(cls, value: float | None) -> float | None:
        if value is not None and value not in (1, 2, 4):
            raise ValueError("speed must be 1, 2, or 4")
        return value

    @model_validator(mode="after")
    def _fields_match_action(self) -> "PlaybackCommand":
        if self.action == "seek":
            if self.position_seconds is None:
                raise ValueError("position_seconds is required for a seek command")
        elif self.position_seconds is not None:
            raise ValueError("position_seconds is only allowed with a seek command")
        if self.action == "set_speed":
            if self.speed is None:
                raise ValueError("speed is required for a set_speed command")
        elif self.speed is not None:
            raise ValueError("speed is only allowed with a set_speed command")
        return self


class PlaybackView(BaseModel):
    run_id: str
    state: Literal["paused", "playing", "ended"]
    position_seconds: float
    duration_seconds: float
    speed: float
    server_time: datetime
    revision: int


class RecordingView(BaseModel):
    id: str
    original_filename: str
    camera_label: str
    duration_seconds: float
    start_offset_seconds: float
    size_bytes: int
    media_url: str
    validation_status: Literal["ready"] = "ready"
    processing_status: Literal["transcribing"] = "transcribing"
    latest_analyzed_time_seconds: float | None = None


class TranscriptTurnView(BaseModel):
    """Anonymous speaker turn within one released segment.

    Speaker numbers are 1-based display labels for this segment only. They do
    not identify officers or witnesses and are not matched across cameras.
    """

    speaker: int
    label: str
    text: str
    local_start_seconds: float
    local_end_seconds: float


class TranscriptSegmentView(BaseModel):
    id: str
    recording_id: str
    run_id: str
    local_start_seconds: float
    local_end_seconds: float
    incident_start_seconds: float
    incident_end_seconds: float
    status: Literal["queued", "processing", "completed", "failed", "empty"]
    text: str | None = None
    error: str | None = None
    turns: list[TranscriptTurnView] = []


class RecordingTranscript(BaseModel):
    recording_id: str
    latest_analyzed_time_seconds: float | None = None
    segments: list[TranscriptSegmentView]


class TranscriptsView(BaseModel):
    run_id: str
    incident_position_seconds: float
    transcription_configured: bool
    recordings: list[RecordingTranscript]


class IncidentSummary(BaseModel):
    id: str
    title: str
    context: str
    created_at: datetime


class IncidentView(IncidentSummary):
    recordings: list[RecordingView]
    playback: PlaybackView

