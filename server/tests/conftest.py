"""
Every test gets an empty database: SQLite in tmp_path by default, or the
Postgres at MERGECLEAR_TEST_DATABASE_URL (tables are dropped and recreated).
Background workers are off; tests run queued jobs with `jobs.run_pending()`.
"""
import os

os.environ["MERGECLEAR_WORKERS"] = "0"

import pytest  # noqa: E402

from app import db  # noqa: E402


def reset_postgres(url):
    """Empty the test database completely (tables and migration history)."""
    from sqlalchemy import create_engine, text

    scratch = create_engine(url)
    with scratch.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    scratch.dispose()


@pytest.fixture(autouse=True)
def database(tmp_path):
    url = os.environ.get("MERGECLEAR_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    if not url.startswith("sqlite"):
        reset_postgres(url)
    engine = db.use(url)                      # runs the migrations, as the server does on start
    import app.main
    app.main._has_users = False          # cached per process; every test starts with an empty database
    from app import limits
    limits.uploads.reset()                    # user ids restart with every database
    yield engine
