"""
Generated documentation, its versions and display titles (database-backed).

An API is identified by (repo, api), where `api` is the stable doc name
(e.g. OrderController.create). A new version is stored only when the API
contract signature changes.
"""
from typing import Dict, Iterator, List, Optional

from sqlalchemy import and_, delete, func, insert, select

from app import db
from app.db import api_titles, api_versions


def is_safe_name(name: str) -> bool:
    """A name usable in URLs and as a path segment (no traversal, no separators)."""
    return bool(name) and len(name) <= 200 and name not in (".", "..") and "/" not in name and "\\" not in name


def _entry(row) -> dict:
    return {
        "version": row.version,
        "signature": row.signature,
        "commit_hash": row.commit,
        "title": row.title,
        "route": row.route,
        "created_at": row.created_at,
    }


# ---------------------------------------------------------------- reads

def list_repos() -> List[str]:
    with db.engine().connect() as conn:
        return list(conn.execute(select(api_versions.c.repo).distinct().order_by(api_versions.c.repo)).scalars())


def list_apis(repo: str) -> List[str]:
    """APIs of a repo that have at least one version, sorted by name."""
    with db.engine().connect() as conn:
        return list(conn.execute(
            select(api_versions.c.api).where(api_versions.c.repo == repo).distinct().order_by(api_versions.c.api)
        ).scalars())


def api_exists(repo: str, api: str) -> bool:
    if not (is_safe_name(repo) and is_safe_name(api)):
        return False
    with db.engine().connect() as conn:
        return conn.execute(
            select(api_versions.c.id).where(api_versions.c.repo == repo, api_versions.c.api == api).limit(1)
        ).first() is not None


def list_versions(repo: str, api: str) -> List[int]:
    """Version numbers of an API, ascending."""
    with db.engine().connect() as conn:
        return list(conn.execute(
            select(api_versions.c.version).where(api_versions.c.repo == repo, api_versions.c.api == api)
            .order_by(api_versions.c.version)
        ).scalars())


def read_version(repo: str, api: str, version) -> Optional[str]:
    try:
        version = int(version)
    except (TypeError, ValueError):
        return None
    with db.engine().connect() as conn:
        return conn.execute(
            select(api_versions.c.content)
            .where(api_versions.c.repo == repo, api_versions.c.api == api, api_versions.c.version == version)
        ).scalar()


def version_entries(repo: str, api: str) -> List[dict]:
    """Every version of an API (without content), ascending."""
    cols = [c for c in api_versions.c if c.name != "content"]
    with db.engine().connect() as conn:
        rows = conn.execute(
            select(*cols).where(api_versions.c.repo == repo, api_versions.c.api == api).order_by(api_versions.c.version)
        )
        return [_entry(r) for r in rows]


def version_entry(repo: str, api: str, version: int) -> Optional[dict]:
    return next((e for e in version_entries(repo, api) if e["version"] == version), None)


def get_route(repo: str, api: str, version) -> Optional[dict]:
    """Structured route data the scanner sent for a version (None for versions without it)."""
    if version is None:
        return None
    with db.engine().connect() as conn:
        return conn.execute(
            select(api_versions.c.route)
            .where(api_versions.c.repo == repo, api_versions.c.api == api, api_versions.c.version == int(version))
        ).scalar()


def latest_versions(repo: str) -> List[dict]:
    """Latest version of every API of a repo in one query: {api, version, versions, commit_hash, route, title, created_at}."""
    latest = (
        select(api_versions.c.api, func.max(api_versions.c.version).label("latest"), func.count().label("versions"))
        .where(api_versions.c.repo == repo).group_by(api_versions.c.api).subquery()
    )
    cols = [c for c in api_versions.c if c.name != "content"]
    query = (
        select(*cols, latest.c.versions)
        .join(latest, and_(api_versions.c.api == latest.c.api, api_versions.c.version == latest.c.latest))
        .where(api_versions.c.repo == repo)
        .order_by(api_versions.c.api)
    )
    with db.engine().connect() as conn:
        return [{**_entry(r), "api": r.api, "versions": r.versions} for r in conn.execute(query)]


def latest_docs(repo: Optional[str] = None) -> Iterator[dict]:
    """Latest documentation of every API (optionally of one repo): {repo, api, version, title, content}."""
    latest = select(api_versions.c.repo, api_versions.c.api, func.max(api_versions.c.version).label("latest"))
    if repo is not None:
        latest = latest.where(api_versions.c.repo == repo)
    latest = latest.group_by(api_versions.c.repo, api_versions.c.api).subquery()
    query = (
        select(api_versions.c.repo, api_versions.c.api, api_versions.c.version, api_versions.c.content,
               api_titles.c.title)
        .join(latest, and_(api_versions.c.repo == latest.c.repo, api_versions.c.api == latest.c.api,
                           api_versions.c.version == latest.c.latest))
        .outerjoin(api_titles, and_(api_titles.c.repo == api_versions.c.repo, api_titles.c.api == api_versions.c.api))
        .order_by(api_versions.c.repo, api_versions.c.api)
    )
    with db.engine().connect() as conn:
        for r in conn.execute(query):
            yield {"repo": r.repo, "api": r.api, "version": r.version, "title": r.title or r.api, "content": r.content}


# ---------------------------------------------------------------- writes

def latest_signature(repo: str, api: str) -> Optional[str]:
    with db.engine().connect() as conn:
        return conn.execute(
            select(api_versions.c.signature).where(api_versions.c.repo == repo, api_versions.c.api == api)
            .order_by(api_versions.c.version.desc()).limit(1)
        ).scalar()


def save_version_if_changed(repo: str, api: str, signature: str, commit: Optional[str], content: str,
                            route: Optional[dict] = None, title: Optional[str] = None) -> Optional[int]:
    """
    Store a new version unless the latest one has the same contract signature.

    Runs under a per-API lock, so concurrent uploads of the same API cannot
    create duplicate or skipped version numbers. Returns the new version
    number, or None when nothing changed.
    """
    with db.engine().begin() as conn:
        with db.lock(conn, f"api:{repo}:{api}"):
            latest = conn.execute(
                select(api_versions.c.version, api_versions.c.signature)
                .where(api_versions.c.repo == repo, api_versions.c.api == api)
                .order_by(api_versions.c.version.desc()).limit(1)
            ).first()
            if latest is not None and latest.signature == signature:
                return None
            version = (latest.version if latest is not None else 0) + 1
            conn.execute(insert(api_versions).values(
                repo=repo, api=api, version=version, signature=signature, commit=commit,
                title=title, route=route, content=content, created_at=db.utcnow(),
            ))
            return version


def delete_repo(repo: str) -> None:
    """Remove everything stored for a repository (docs, titles, QA plans, templates, architecture)."""
    from app.db import architectures, qa_plans, test_templates

    with db.engine().begin() as conn:
        for table in (api_versions, api_titles, qa_plans, test_templates, architectures):
            conn.execute(delete(table).where(table.c.repo == repo))


# ---------------------------------------------------------------- display titles
# The doc name stays the stable key for URLs and versions; titles are display names.

def get_titles(repo: str) -> Dict[str, str]:
    """{doc name: display title}"""
    with db.engine().connect() as conn:
        return {r.api: r.title for r in conn.execute(select(api_titles).where(api_titles.c.repo == repo)) if r.title}


def get_title(repo: str, api: str) -> str:
    entry = title_entry(repo, api)
    return (entry or {}).get("title") or api


def title_entry(repo: str, api: str) -> Optional[dict]:
    with db.engine().connect() as conn:
        row = conn.execute(select(api_titles).where(api_titles.c.repo == repo, api_titles.c.api == api)).first()
    return {"title": row.title, "source": row.source} if row else None


def set_title(repo: str, api: str, title: str, source: str):
    if not title:
        return
    with db.engine().begin() as conn:
        db.upsert(conn, api_titles, {"repo": repo, "api": api, "title": title, "source": source}, ("repo", "api"))
