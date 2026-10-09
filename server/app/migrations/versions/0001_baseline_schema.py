"""baseline schema: every table as of Mergeclear 0.2

Revision ID: 0001
Revises: 
Create Date: 2026-10-09 07:05:13.109703
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def _exists(table):
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade():
    # Tables are only created when missing: databases made before migrations
    # existed (tables created on start) adopt this baseline without changes.
    if not _exists('api_titles'):
        op.create_table('api_titles',
        sa.Column('repo', sa.String(length=200), nullable=False),
        sa.Column('api', sa.String(length=400), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('repo', 'api')
        )

    if not _exists('api_versions'):
        op.create_table('api_versions',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('repo', sa.String(length=200), nullable=False),
        sa.Column('api', sa.String(length=400), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('signature', sa.String(length=64), nullable=False),
        sa.Column('commit', sa.String(length=200), nullable=True),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('route', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('repo', 'api', 'version', name='uq_api_version')
        )
        with op.batch_alter_table('api_versions', schema=None) as batch_op:
            batch_op.create_index('ix_api_versions_repo_api', ['repo', 'api'], unique=False)

    if not _exists('architectures'):
        op.create_table('architectures',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('repo', sa.String(length=200), nullable=False),
        sa.Column('commit', sa.String(length=200), nullable=True),
        sa.Column('document', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('architectures', schema=None) as batch_op:
            batch_op.create_index('ix_architectures_repo', ['repo', 'id'], unique=False)

    if not _exists('jobs'):
        op.create_table('jobs',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('repo', sa.String(length=200), nullable=True),
        sa.Column('commit', sa.String(length=200), nullable=True),
        sa.Column('dedupe_key', sa.String(length=64), nullable=False),
        sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('progress_done', sa.Integer(), nullable=False),
        sa.Column('progress_total', sa.Integer(), nullable=False),
        sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker', sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('jobs', schema=None) as batch_op:
            batch_op.create_index('ix_jobs_dedupe', ['dedupe_key'], unique=False)
            batch_op.create_index('ix_jobs_pick', ['status', 'run_after'], unique=False)
            batch_op.create_index('ix_jobs_repo', ['repo', 'id'], unique=False)

    if not _exists('qa_plans'):
        op.create_table('qa_plans',
        sa.Column('repo', sa.String(length=200), nullable=False),
        sa.Column('api', sa.String(length=400), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('plan', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('api_doc_hash', sa.String(length=64), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('repo', 'api', 'version')
        )

    if not _exists('test_templates'):
        op.create_table('test_templates',
        sa.Column('repo', sa.String(length=200), nullable=False),
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('test_cases', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('repo', 'key')
        )

    if not _exists('users'):
        op.create_table('users',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('password_hash', sa.String(length=300), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
        )

    if not _exists('api_keys'):
        op.create_table('api_keys',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('prefix', sa.String(length=32), nullable=False),
        sa.Column('secret_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('prefix')
        )
        with op.batch_alter_table('api_keys', schema=None) as batch_op:
            batch_op.create_index('ix_api_keys_user', ['user_id'], unique=False)

    if not _exists('sessions'):
        op.create_table('sessions',
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('csrf_token', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('token_hash')
        )
        with op.batch_alter_table('sessions', schema=None) as batch_op:
            batch_op.create_index('ix_sessions_user', ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index('ix_sessions_user')

    op.drop_table('sessions')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index('ix_api_keys_user')

    op.drop_table('api_keys')
    op.drop_table('users')
    op.drop_table('test_templates')
    op.drop_table('qa_plans')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_repo')
        batch_op.drop_index('ix_jobs_pick')
        batch_op.drop_index('ix_jobs_dedupe')

    op.drop_table('jobs')
    with op.batch_alter_table('architectures', schema=None) as batch_op:
        batch_op.drop_index('ix_architectures_repo')

    op.drop_table('architectures')
    with op.batch_alter_table('api_versions', schema=None) as batch_op:
        batch_op.drop_index('ix_api_versions_repo_api')

    op.drop_table('api_versions')
    op.drop_table('api_titles')
