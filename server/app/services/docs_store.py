"""
Read access to generated docs laid out as DOCS_DIR/<repo>/<api>/v<N>.md.
"""
import json
import os
from typing import Dict, List, Optional

from app.config import DOCS_DIR


def is_safe_name(name: str) -> bool:
    """A single path segment that cannot escape its parent directory."""
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name


def _visible_dirs(path: str) -> List[str]:
    if not os.path.isdir(path):
        return []

    return [
        d for d in os.listdir(path)
        if not d.startswith(".") and os.path.isdir(os.path.join(path, d))
    ]


def _version_number(filename: str) -> Optional[int]:
    if not (filename.startswith("v") and filename.endswith(".md")):
        return None

    try:
        return int(filename[1:-3])
    except ValueError:
        return None


def api_path(repo: str, api: str) -> Optional[str]:
    """Directory of an API's docs, or None if it does not exist."""
    if not (is_safe_name(repo) and is_safe_name(api)):
        return None

    path = os.path.join(DOCS_DIR, repo, api)
    return path if os.path.isdir(path) else None


def list_repos() -> List[str]:
    return sorted(_visible_dirs(DOCS_DIR))


def list_versions(repo: str, api: str) -> List[int]:
    """Version numbers of an API, ascending."""
    path = api_path(repo, api)

    if not path:
        return []

    versions = (_version_number(f) for f in os.listdir(path))
    return sorted(v for v in versions if v is not None)


def list_apis(repo: str) -> List[str]:
    """APIs of a repo that have at least one version, sorted by name."""
    if not is_safe_name(repo):
        return []

    return sorted(
        api for api in _visible_dirs(os.path.join(DOCS_DIR, repo))
        if list_versions(repo, api)
    )


def read_version(repo: str, api: str, version) -> Optional[str]:
    path = api_path(repo, api)

    if not path:
        return None

    file_path = os.path.join(path, f"v{version}.md")

    if not os.path.isfile(file_path):
        return None

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ============================
# DISPLAY TITLES
# ============================
# The doc name (e.g. OwnerController.processFindForm) stays the stable key for
# folders, URLs and versions; titles are display names kept beside the docs.

TITLES_FILE = ".titles.json"


def _titles_path(repo: str) -> Optional[str]:
    if not is_safe_name(repo):
        return None
    return os.path.join(DOCS_DIR, repo, TITLES_FILE)


def _load_titles(repo: str) -> Dict[str, dict]:
    path = _titles_path(repo)
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_titles(repo: str) -> Dict[str, str]:
    """{doc name: display title}"""
    return {api: entry.get("title") for api, entry in _load_titles(repo).items() if entry.get("title")}


def get_title(repo: str, api: str) -> str:
    return get_titles(repo).get(api) or api


def title_entry(repo: str, api: str) -> Optional[dict]:
    return _load_titles(repo).get(api)


def set_title(repo: str, api: str, title: str, source: str):
    path = _titles_path(repo)
    if not path or not title:
        return
    titles = _load_titles(repo)
    titles[api] = {"title": title, "source": source}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(titles, f, indent=2, sort_keys=True)
