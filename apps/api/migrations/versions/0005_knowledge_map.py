"""Versioned knowledge maps with normalized observations and source joins."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("knowledge_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("playback_runs.id"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(36)), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(500)), sa.UniqueConstraint("run_id", "input_hash", name="uq_knowledge_input"))
    op.create_index("ix_knowledge_versions_run_id", "knowledge_versions", ["run_id"])
    op.create_table("knowledge_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("knowledge_versions.id"), nullable=False),
        sa.Column("segment_id", sa.String(36), sa.ForeignKey("transcript_segments.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False), sa.Column("attribution", sa.String(200), nullable=False))
    op.create_index("ix_knowledge_observations_version_id", "knowledge_observations", ["version_id"])
    op.create_table("knowledge_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("knowledge_versions.id"), nullable=False),
        sa.Column("item_key", sa.String(100), nullable=False), sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("label", sa.String(200), nullable=False), sa.Column("description", sa.Text(), nullable=False),
        sa.Column("uncertainty", sa.String(400), nullable=False),
        sa.Column("source_key", sa.String(100)), sa.Column("target_key", sa.String(100)),
        sa.UniqueConstraint("version_id", "item_key", name="uq_knowledge_item"))
    op.create_index("ix_knowledge_items_version_id", "knowledge_items", ["version_id"])
    op.create_table("knowledge_supports",
        sa.Column("item_id", sa.String(36), sa.ForeignKey("knowledge_items.id"), primary_key=True),
        sa.Column("observation_id", sa.String(36), sa.ForeignKey("knowledge_observations.id"), primary_key=True))


def downgrade():
    for table in ("knowledge_supports", "knowledge_items", "knowledge_observations", "knowledge_versions"):
        op.drop_table(table)
