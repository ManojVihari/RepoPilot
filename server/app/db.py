"""
Database: Postgres in production (MERGECLEAR_DATABASE_URL), SQLite as the
zero-setup default for local runs and tests. One schema, one code path
(SQLAlchemy Core); the few Postgres-only features (advisory locks, SKIP
LOCKED) have SQLite equivalents that are safe because SQLite serialises
writers.
"""
import contextlib
import hashlib
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, Text,
                        UniqueConstraint, create_engine, event, text)
from sqlalchemy.dialects.postgresql import JSONB

from app.config import DATABASE_DIR, DATABASE_URL

metadata = MetaData()
Json = JSON().with_variant(JSONB(), "postgresql")
Id = BigInteger().with_variant(Integer(), "sqlite")     # SQLite autoincrements INTEGER PRIMARY KEY only


def utcnow():
    return datetime.now(timezone.utc)


def Timestamp(**kw):
    return Column(DateTime(timezone=True), default=utcnow, **kw)


# one row per documented version of an API; content is the generated markdown
api_versions = Table(
    "api_versions", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("repo", String(200), nullable=False),
    Column("api", String(400), nullable=False),
    Column("version", Integer, nullable=False),
    Column("signature", String(64), nullable=False),
    Column("commit", String(200)),
    Column("title", Text),
    Column("route", Json),
    Column("content", Text, nullable=False),
    Timestamp(name="created_at", nullable=False),
    UniqueConstraint("repo", "api", "version", name="uq_api_version"),
    Index("ix_api_versions_repo_api", "repo", "api"),
)

api_titles = Table(
    "api_titles", metadata,
    Column("repo", String(200), primary_key=True),
    Column("api", String(400), primary_key=True),
    Column("title", Text, nullable=False),
    Column("source", String(20), nullable=False),
)

architectures = Table(
    "architectures", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("repo", String(200), nullable=False),
    Column("commit", String(200)),
    Column("document", Json, nullable=False),
    Timestamp(name="created_at", nullable=False),
    Index("ix_architectures_repo", "repo", "id"),
)

qa_plans = Table(
    "qa_plans", metadata,
    Column("repo", String(200), primary_key=True),
    Column("api", String(400), primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("plan", Json, nullable=False),
    Column("api_doc_hash", String(64)),
    Timestamp(name="generated_at", nullable=False),
)

test_templates = Table(
    "test_templates", metadata,
    Column("repo", String(200), primary_key=True),
    Column("key", String(200), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("category", String(100)),
    Column("description", Text),
    Column("test_cases", Json, nullable=False),
    Timestamp(name="created_at", nullable=False),
)

# background work (documenting an uploaded scan); see app/jobs.py
jobs = Table(
    "jobs", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("kind", String(40), nullable=False),
    Column("repo", String(200)),
    Column("commit", String(200)),
    Column("dedupe_key", String(64), nullable=False),
    Column("payload", Json, nullable=False),
    Column("status", String(20), nullable=False, default="queued"),   # queued | running | done | failed
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("error", Text),
    Column("progress_done", Integer, nullable=False, default=0),
    Column("progress_total", Integer, nullable=False, default=0),
    Column("result", Json),
    Timestamp(name="created_at", nullable=False),
    Column("run_after", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    Column("lease_until", DateTime(timezone=True)),
    Column("worker", String(100)),
    Index("ix_jobs_pick", "status", "run_after"),
    Index("ix_jobs_dedupe", "dedupe_key"),
    Index("ix_jobs_repo", "repo", "id"),
)


# accounts: people sign in with email + password; scanners use API keys
users = Table(
    "users", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("email", String(320), nullable=False, unique=True),       # stored lower-case
    Column("name", String(200), nullable=False),
    Column("role", String(20), nullable=False),                      # admin | viewer
    Column("password_hash", String(300), nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("must_change_password", Boolean, nullable=False, default=False),
    Timestamp(name="created_at", nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
)

# browser sessions; only a hash of the cookie value is stored
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Id, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", String(64), nullable=False),
    Timestamp(name="created_at", nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_sessions_user", "user_id"),
)

# API keys for scanners and scripts; act with their owner's role
api_keys = Table(
    "api_keys", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("user_id", Id, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(100), nullable=False),
    # project that uploads with this key go to when the pipeline does not name one
    Column("project", String(200)),
    Column("prefix", String(32), nullable=False, unique=True),       # shown in lists, looked up on use
    Column("secret_hash", String(64), nullable=False),
    Timestamp(name="created_at", nullable=False),
    Column("last_used_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Index("ix_api_keys_user", "user_id"),
)


# ---------------------------------------------------------------- engine

_engine = None
_engine_lock = threading.Lock()


def database_url() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    os.makedirs(DATABASE_DIR, exist_ok=True)
    return "sqlite:///" + os.path.join(DATABASE_DIR, "mergeclear.db")


def _sqlite_pragmas(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")       # readers do not block the writer
    cursor.execute("PRAGMA busy_timeout=15000")     # wait for the writer instead of failing
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
_MIGRATION_LOCK = 72_656_701      # any constant: one Postgres advisory lock for schema changes


def migrate(target_engine, revision: str = "head"):
    """
    Bring the schema up to date (Alembic). Runs in one transaction; on Postgres
    under an advisory lock, so a server and workers starting together migrate once.
    """
    import logging

    from alembic import command
    from alembic.config import Config

    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)   # setup chatter on every start
    config = Config()
    config.set_main_option("script_location", MIGRATIONS_DIR)
    with target_engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _MIGRATION_LOCK})
        config.attributes["connection"] = conn
        command.upgrade(config, revision)


def schema_revision(target_engine=None) -> str:
    """The migration the database is at (None before the first one)."""
    from alembic.migration import MigrationContext

    with (target_engine or engine()).connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                created = make_engine(database_url())
                migrate(created)
                _engine = created
    return _engine


def use(url: str):
    """Point the app at another database (tests, the import command)."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = make_engine(url)
        migrate(_engine)
    return _engine


def is_postgres() -> bool:
    return engine().dialect.name == "postgresql"


def describe() -> str:
    """The database in use, without credentials."""
    url = engine().url
    return url.render_as_string(hide_password=True)


@contextlib.contextmanager
def lock(conn, key: str):
    """
    Serialise work on `key` (e.g. one API's versions) until the transaction ends.
    Postgres: transaction-scoped advisory lock. SQLite: a write lock on the database.
    """
    if conn.dialect.name == "postgresql":
        digest = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": digest})
    else:
        # an UPDATE that changes nothing still takes SQLite's write lock for the rest of the transaction
        conn.execute(text("UPDATE api_titles SET source = source WHERE 0"))
    yield


def healthy() -> bool:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def upsert(conn, table, values: dict, keys):
    """INSERT ... ON CONFLICT (keys) DO UPDATE, on Postgres and SQLite."""
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    statement = dialect_insert(table).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=list(keys),
        set_={k: statement.excluded[k] for k in values if k not in keys},
    )
    conn.execute(statement)
