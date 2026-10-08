"""
Application models sent by the scanner, stored per repository:
DATABASE_DIR/architecture/<repo>/latest.json and <commit>.json
"""
import json
import os
import re
from typing import Optional

from app.config import DATABASE_DIR
from app.services.docs_store import is_safe_name

ARCHITECTURE_DIR = os.path.join(DATABASE_DIR, "architecture")


def _commit_name(commit: str) -> Optional[str]:
    name = re.sub(r"[^\w.-]", "_", commit or "")
    return name if is_safe_name(name) else None


def save_architecture(repository: str, commit: str, architecture: dict) -> bool:
    if not architecture or not is_safe_name(repository):
        return False

    repo_dir = os.path.join(ARCHITECTURE_DIR, repository)
    os.makedirs(repo_dir, exist_ok=True)

    document = {"repository": repository, "commit": commit, "architecture": architecture}
    names = ["latest.json"]
    commit_name = _commit_name(commit)
    if commit_name:
        names.append(f"{commit_name}.json")

    for name in names:
        with open(os.path.join(repo_dir, name), "w", encoding="utf-8") as f:
            json.dump(document, f)

    return True


def has_architecture(repository: str) -> bool:
    return is_safe_name(repository) and os.path.isfile(os.path.join(ARCHITECTURE_DIR, repository, "latest.json"))


def load_architecture(repository: str, commit: Optional[str] = None) -> Optional[dict]:
    if not is_safe_name(repository):
        return None

    name = f"{_commit_name(commit)}.json" if commit else "latest.json"
    if commit and not _commit_name(commit):
        return None

    path = os.path.join(ARCHITECTURE_DIR, repository, name)
    if not os.path.isfile(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
