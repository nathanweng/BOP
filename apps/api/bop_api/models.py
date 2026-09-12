from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    context: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active_run_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("playback_runs.id", name="fk_incidents_active_run", use_alter=True),
        nullable=True,
    )


class PlaybackRun(Base):
    __tablename__ = "playback_runs"
    __table_args__ = (
        CheckConstraint("position_seconds >= 0", name="ck_run_position_nonnegative"),
        CheckConstraint("revision >= 0", name="ck_run_revision_nonnegative"),
        CheckConstraint("state IN ('paused', 'playing', 'ended')", name="ck_run_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(12), default="paused")
    position_seconds: Mapped[float] = mapped_column(Float, default=0)
    anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=0)


class Recording(Base):
    __tablename__ = "recordings"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="ck_recording_duration_positive"),
        CheckConstraint("start_offset_seconds >= 0 AND start_offset_seconds <= 86400", name="ck_recording_offset"),
        CheckConstraint("size_bytes > 0", name="ck_recording_size_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    camera_label: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(40), unique=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    start_offset_seconds: Mapped[float] = mapped_column(Float, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'empty')",
            name="ck_transcript_status",
        ),
        CheckConstraint(
            "local_end_seconds > local_start_seconds",
            name="ck_transcript_window_positive",
        ),
        UniqueConstraint(
            "run_id", "recording_id", "local_start_seconds", "local_end_seconds",
            name="uq_transcript_run_recording_window",
        ),
        Index(
            "ix_transcript_run_recording",
            "run_id", "recording_id", "incident_start_seconds",
        ),
        Index("ix_transcript_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("playback_runs.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    local_start_seconds: Mapped[float] = mapped_column(Float)
    local_end_seconds: Mapped[float] = mapped_column(Float)
    incident_start_seconds: Mapped[float] = mapped_column(Float)
    incident_end_seconds: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    words_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
