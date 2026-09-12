"""Durable board progress, source registry and replay schedule."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('knowledge_versions', sa.Column('stage', sa.String(32), nullable=False, server_default='queued'))
    op.add_column('knowledge_versions', sa.Column('completed_batches', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('knowledge_versions', sa.Column('total_batches', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('knowledge_versions', sa.Column('replay_json', sa.Text()))
    op.add_column('knowledge_versions', sa.Column('sources_json', sa.Text()))


def downgrade():
    for name in ('sources_json', 'replay_json', 'total_batches', 'completed_batches', 'stage'):
        op.drop_column('knowledge_versions', name)
