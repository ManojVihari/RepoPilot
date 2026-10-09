"""Database migrations: the schema they build matches the models, and older databases are adopted."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.config import Config
from sqlalchemy import create_engine, insert, inspect, select

from app import db


def _head():
    config = Config()
    config.set_main_option("script_location", db.MIGRATIONS_DIR)
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"migrations have several heads {heads}: merge them (alembic merge)"
    return heads[0]


def test_migrations_build_exactly_the_models(database):
    """Fails when db.py changes without a migration: run `alembic revision --autogenerate`."""
    with database.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        differences = compare_metadata(context, db.metadata)
    assert differences == [], f"schema differs from the models: {differences}"
    assert db.schema_revision(database) == _head()


def test_a_database_from_before_migrations_is_adopted(tmp_path):
    """Databases whose tables were created on start (0.2 and earlier) upgrade in place, keeping their data."""
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    old_tables = [t for name, t in db.metadata.tables.items() if name not in ("users", "sessions", "api_keys")]
    db.metadata.create_all(legacy, tables=old_tables)          # e.g. before accounts existed
    with legacy.begin() as conn:
        conn.execute(insert(db.api_titles).values(repo="shop", api="Orders.create", title="Place order", source="llm"))
    assert db.schema_revision(legacy) is None

    db.migrate(legacy)

    assert db.schema_revision(legacy) == _head()
    assert {"users", "sessions", "api_keys", "api_versions"} <= set(inspect(legacy).get_table_names())
    with legacy.connect() as conn:
        assert conn.execute(select(db.api_titles.c.title)).scalar() == "Place order"
    db.migrate(legacy)                                          # running again changes nothing
    legacy.dispose()
