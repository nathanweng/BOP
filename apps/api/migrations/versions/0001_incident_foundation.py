"""Incident media and authoritative playback runs only.

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_run_id", sa.String(36), nullable=True),
    )
    op.create_table(
        "playback_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("position_seconds", sa.Float(), nullable=False),
        sa.Column("anchor_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("position_seconds >= 0", name="ck_run_position_nonnegative"),
        sa.CheckConstraint("revision >= 0", name="ck_run_revision_nonnegative"),
        sa.CheckConstraint("state IN ('paused', 'playing', 'ended')", name="ck_run_state"),
    )
    op.create_index("ix_playback_runs_incident_id", "playback_runs", ["incident_id"])
    with op.batch_alter_table("incidents") as batch:
        batch.create_foreign_key("fk_incidents_active_run", "playback_runs", ["active_run_id"], ["id"])
    op.create_table(
        "recordings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("camera_label", sa.String(100), nullable=False),
        sa.Column("storage_key", sa.String(40), nullable=False, unique=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("start_offset_seconds", sa.Float(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("duration_seconds > 0", name="ck_recording_duration_positive"),
        sa.CheckConstraint("start_offset_seconds >= 0 AND start_offset_seconds <= 86400", name="ck_recording_offset"),
        sa.CheckConstraint("size_bytes > 0", name="ck_recording_size_positive"),
    )
    op.create_index("ix_recordings_incident_id", "recordings", ["incident_id"])


def downgrade():
    op.drop_table("recordings")
    with op.batch_alter_table("incidents") as batch:
        batch.drop_constraint("fk_incidents_active_run", type_="foreignkey")
    op.drop_table("playback_runs")
    op.drop_table("incidents")
