from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    action: Literal["play", "pause", "restart"]
    expected_revision: int = Field(ge=0, strict=True)


class PlaybackView(BaseModel):
    run_id: str
    state: Literal["paused", "playing", "ended"]
    position_seconds: float
    duration_seconds: float
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
    processing_status: Literal["not_implemented"] = "not_implemented"
    latest_analyzed_time_seconds: None = None


class IncidentSummary(BaseModel):
    id: str
    title: str
    context: str
    created_at: datetime


class IncidentView(IncidentSummary):
    recordings: list[RecordingView]
    playback: PlaybackView

