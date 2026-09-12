"""Versioned personnel situation reports with durable worker ownership."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("situation_report_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("playback_runs.id"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("known_through", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload_json", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(500)),
        sa.UniqueConstraint("run_id", "input_hash", name="uq_situation_report_input"))
    op.create_index("ix_situation_report_versions_run_id", "situation_report_versions", ["run_id"])


def downgrade():
    op.drop_table("situation_report_versions")
