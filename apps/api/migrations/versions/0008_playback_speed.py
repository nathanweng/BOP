"""Shared demo playback speeds on the incident clock.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('playback_runs') as batch:
        batch.add_column(sa.Column('speed', sa.Float(), nullable=False, server_default='1'))
        batch.create_check_constraint('ck_run_speed', 'speed IN (1, 2, 4)')


def downgrade():
    with op.batch_alter_table('playback_runs') as batch:
        batch.drop_constraint('ck_run_speed', type_='check')
        batch.drop_column('speed')
