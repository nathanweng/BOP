"""Durable incremental event processing and worker leases."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("event_histories", sa.Column("processed_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("event_histories", sa.Column("lease_token", sa.String(36), nullable=True))
    op.add_column("event_histories", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("event_histories", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("event_histories", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("event_histories", sa.Column("error", sa.String(500), nullable=True))


def downgrade():
    for name in ("error", "attempts", "retry_at", "lease_until", "lease_token", "processed_json"):
        op.drop_column("event_histories", name)
