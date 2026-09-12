"""Per-run, per-recording live transcript segments.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("playback_runs.id"), nullable=False),
        sa.Column("recording_id", sa.String(36), sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("local_start_seconds", sa.Float(), nullable=False),
        sa.Column("local_end_seconds", sa.Float(), nullable=False),
        sa.Column("incident_start_seconds", sa.Float(), nullable=False),
        sa.Column("incident_end_seconds", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("words_json", sa.Text(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'empty')",
            name="ck_transcript_status",
        ),
        sa.CheckConstraint(
            "local_end_seconds > local_start_seconds",
            name="ck_transcript_window_positive",
        ),
        sa.UniqueConstraint(
            "run_id", "recording_id", "local_start_seconds", "local_end_seconds",
            name="uq_transcript_run_recording_window",
        ),
    )
    op.create_index("ix_transcript_segments_run_id", "transcript_segments", ["run_id"])
    op.create_index("ix_transcript_segments_recording_id", "transcript_segments", ["recording_id"])
    op.create_index(
        "ix_transcript_run_recording",
        "transcript_segments",
        ["run_id", "recording_id", "incident_start_seconds"],
    )
    op.create_index(
        "ix_transcript_status_created",
        "transcript_segments",
        ["status", "created_at"],
    )


def downgrade():
    op.drop_index("ix_transcript_status_created", table_name="transcript_segments")
    op.drop_index("ix_transcript_run_recording", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_recording_id", table_name="transcript_segments")
    op.drop_index("ix_transcript_segments_run_id", table_name="transcript_segments")
    op.drop_table("transcript_segments")
