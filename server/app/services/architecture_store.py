"""
Application models sent by the scanner, one row per upload; the newest row of
a repository is its current architecture.
"""
from typing import Optional

from sqlalchemy import insert, select

from app import db
from app.db import architectures
from app.services.docs_store import is_safe_name


def save_architecture(repository: str, commit: str, architecture: dict) -> bool:
    if not architecture or not is_safe_name(repository):
        return False
    document = {"repository": repository, "commit": commit, "architecture": architecture}
    with db.engine().begin() as conn:
        conn.execute(insert(architectures).values(repo=repository, commit=commit, document=document, created_at=db.utcnow()))
    return True


def has_architecture(repository: str) -> bool:
    with db.engine().connect() as conn:
        return conn.execute(select(architectures.c.id).where(architectures.c.repo == repository).limit(1)).first() is not None


def load_architecture(repository: str, commit: Optional[str] = None) -> Optional[dict]:
    """Latest application model of a repository, or the one uploaded for `commit`."""
    if not is_safe_name(repository):
        return None
    query = select(architectures.c.document).where(architectures.c.repo == repository)
    if commit:
        query = query.where(architectures.c.commit == commit)
    with db.engine().connect() as conn:
        return conn.execute(query.order_by(architectures.c.id.desc()).limit(1)).scalar()
