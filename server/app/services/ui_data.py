"""
Read-only view models for the UI: API lists with method/path, repository
summaries and recent changes. Everything comes from the docs folder, the
version files and the stored architecture model.
"""
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services import docs_store
from app.services.architecture_store import has_architecture, load_architecture
from app.services import doc_service


def _versions(repo, api):
    # the instance that writes versions, so reads always see the same storage
    return doc_service.version_service.get_versions(repo, api)


def _mtime(repo: str, api: str, version: int) -> Optional[float]:
    path = os.path.join(docs_store.DOCS_DIR, repo, api, f"v{version}.md")
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def relative_time(timestamp: Optional[float]) -> str:
    if not timestamp:
        return ""
    seconds = max(0, datetime.now(timezone.utc).timestamp() - timestamp)
    for unit, size in (("year", 31536000), ("month", 2592000), ("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return "just now"


def absolute_time(timestamp: Optional[float]) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _group_of(route: dict, api: str) -> str:
    controller = (route or {}).get("controller") or ""
    if controller:
        return controller.rsplit(".", 1)[-1]
    handler = (route or {}).get("handler") or ""
    if "." in handler:
        return handler.split(".", 1)[0]
    return "APIs"


def api_items(repo: str) -> List[dict]:
    """Every documented API of a repo with title, method, path and freshness."""
    titles = docs_store.get_titles(repo)
    items = []

    for api in docs_store.list_apis(repo):
        versions = docs_store.list_versions(repo, api)
        latest = versions[-1]
        entries = _versions(repo, api)
        entry = next((e for e in reversed(entries) if e.get("version") == latest), entries[-1] if entries else {})
        route = entry.get("route") or {}
        updated = _mtime(repo, api, latest)

        items.append({
            "name": api,
            "title": titles.get(api, api),
            "method": route.get("method"),
            "path": route.get("path"),
            "context_path": route.get("context_path"),
            "handler": route.get("handler"),
            "module": route.get("module"),
            "group": _group_of(route, api),
            "latest_version": latest,
            "versions": len(versions),
            "commit": entry.get("commit_hash"),
            "updated": updated,
            "updated_label": relative_time(updated),
            "updated_title": absolute_time(updated),
        })

    return sorted(items, key=lambda i: (i["group"].lower(), i["title"].lower()))


def current_route(repo: str, api: str, version: Optional[int] = None) -> dict:
    versions = docs_store.list_versions(repo, api)
    if not versions:
        return {}
    wanted = version or versions[-1]
    entries = _versions(repo, api)
    entry = next((e for e in entries if e.get("version") == wanted), None)
    return (entry or {}).get("route") or {}


def version_rows(repo: str, api: str) -> List[dict]:
    """Versions newest first with commit, title and time."""
    entries = {e.get("version"): e for e in _versions(repo, api)}
    rows = []
    versions = docs_store.list_versions(repo, api)

    for i, v in enumerate(versions):
        entry = entries.get(v, {})
        updated = _mtime(repo, api, v)
        route = entry.get("route") or {}
        rows.append({
            "version": v,
            "previous": versions[i - 1] if i > 0 else None,
            "commit": entry.get("commit_hash"),
            "title": entry.get("title"),
            "change_reasons": route.get("change_reasons") or [],
            "breaking_changes": route.get("breaking_changes") or [],
            "updated_label": relative_time(updated),
            "updated_title": absolute_time(updated),
            "is_latest": i == len(versions) - 1,
        })

    return list(reversed(rows))


def repo_summary(repo: str, items: Optional[List[dict]] = None) -> dict:
    items = items if items is not None else api_items(repo)
    updated = max((i["updated"] for i in items if i["updated"]), default=None)
    summary = {
        "name": repo,
        "api_count": len(items),
        "version_count": sum(i["versions"] for i in items),
        "changed_apis": sum(1 for i in items if i["versions"] > 1),
        "updated": updated,
        "updated_label": relative_time(updated),
        "updated_title": absolute_time(updated),
        "has_architecture": has_architecture(repo),
        "architecture": None,
    }

    if summary["has_architecture"]:
        document = load_architecture(repo) or {}
        model = (document.get("architecture") or {}).get("spring") or {}
        s = model.get("summary") or {}
        technologies = s.get("technologies") or {}
        summary["architecture"] = {
            "commit": document.get("commit"),
            "style": s.get("architecture_style"),
            "modules": s.get("modules"),
            "endpoints": s.get("endpoints"),
            "entities": s.get("entities"),
            "technologies": [t for cat in ("web", "database", "cache", "messaging", "http-client", "security", "service-discovery")
                             for t in technologies.get(cat, [])][:8],
        }

    return summary


def recent_changes(repos: List[str], limit: int = 8, items_by_repo: Optional[Dict[str, List[dict]]] = None) -> List[dict]:
    """Latest documented versions across repositories, newest first."""
    changes = []
    for repo in repos:
        items = (items_by_repo or {}).get(repo) or api_items(repo)
        for item in items:
            changes.append({
                "repo": repo,
                "api": item["name"],
                "title": item["title"],
                "method": item["method"],
                "version": item["latest_version"],
                "previous": item["latest_version"] - 1 if item["latest_version"] > 1 else None,
                "updated": item["updated"],
                "updated_label": item["updated_label"],
                "updated_title": item["updated_title"],
            })
    changes.sort(key=lambda c: c["updated"] or 0, reverse=True)
    return changes[:limit]
