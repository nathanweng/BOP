"""Persist source-linked event histories per playback run."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_histories",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("playback_runs.id"), primary_key=True),
        sa.Column("events_json", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("event_histories")
