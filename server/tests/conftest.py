"""
Every test gets an empty database: SQLite in tmp_path by default, or the
Postgres at MERGECLEAR_TEST_DATABASE_URL (tables are dropped and recreated).
Background workers are off; tests run queued jobs with `jobs.run_pending()`.
"""
import os

os.environ["MERGECLEAR_WORKERS"] = "0"

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def database(tmp_path):
    url = os.environ.get("MERGECLEAR_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    engine = db.use(url)
    if engine.dialect.name != "sqlite":
        db.metadata.drop_all(engine)
        db.metadata.create_all(engine)
    yield engine
