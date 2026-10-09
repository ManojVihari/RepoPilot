"""
Alembic environment. Runs against the connection the app hands over
(app.db.migrate) or, from the alembic CLI, the app's own database URL.
"""
from alembic import context

from app import db

config = context.config
target_metadata = db.metadata


def _configure(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",   # SQLite cannot ALTER most things in place
        compare_type=True,
    )


connection = config.attributes.get("connection")
if connection is not None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()
else:
    with db.make_engine(db.database_url()).connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()
