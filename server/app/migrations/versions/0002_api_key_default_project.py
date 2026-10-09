"""api key default project: uploads without --project go to the key's project

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 07:51:34.236926
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.add_column(sa.Column('project', sa.String(length=200), nullable=True))



def downgrade():
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_column('project')

