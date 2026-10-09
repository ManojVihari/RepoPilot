import os

# Directories that never contain the project's own API sources.
SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "venv", ".venv", "env", ".env", "site-packages", "__pycache__",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    "node_modules", "target", "build", "dist", ".gradle", ".mvn", ".idea",
}


def iter_source_files(repo_path, extension):
    """Yield paths of files ending with `extension`, pruning SKIP_DIRS."""

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for f in files:
            if f.endswith(extension):
                yield os.path.join(root, f)
