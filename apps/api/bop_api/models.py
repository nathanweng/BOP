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


class EventHistory(Base):
    __tablename__ = "event_histories"

    run_id: Mapped[str] = mapped_column(ForeignKey("playback_runs.id"), primary_key=True)
    events_json: Mapped[str] = mapped_column(Text)
    processed_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (UniqueConstraint("run_id", "input_hash", name="uq_knowledge_input"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("playback_runs.id"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    completed_batches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_batches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    replay_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeObservation(Base):
    __tablename__ = "knowledge_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), index=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id"))
    text: Mapped[str] = mapped_column(Text)
    attribution: Mapped[str] = mapped_column(String(200))


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (UniqueConstraint("version_id", "item_key", name="uq_knowledge_item"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("knowledge_versions.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    uncertainty: Mapped[str] = mapped_column(String(400))
    source_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_key: Mapped[str | None] = mapped_column(String(100), nullable=True)


class KnowledgeSupport(Base):
    __tablename__ = "knowledge_supports"
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"), primary_key=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("knowledge_observations.id"), primary_key=True)


class SituationReportVersion(Base):
    __tablename__ = "situation_report_versions"
    __table_args__ = (UniqueConstraint("run_id", "input_hash", name="uq_situation_report_input"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("playback_runs.id"), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    known_through: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
