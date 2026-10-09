"""Server-wide settings, from environment variables. Paths are absolute so the server works from any cwd."""
import os

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env(name, default=None):
    """MERGECLEAR_<name>; the pre-rename DOCAI_<name> still works for existing setups."""
    return os.environ.get(f"MERGECLEAR_{name}") or os.environ.get(f"DOCAI_{name}") or default


# where generated docs and version metadata live
DOCS_DIR = env("DOCS_DIR") or os.path.join(SERVER_DIR, "docs")
DATABASE_DIR = env("DATABASE_DIR") or os.path.join(SERVER_DIR, "database")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral")

# MERGECLEAR_LLM=off generates docs from scanner data only (no Ollama calls)
LLM_ENABLED = (env("LLM", "on") or "on").lower() not in ("off", "0", "false", "no")

# MERGECLEAR_TOKEN: when set, scanners must send it (Authorization: Bearer ...) to upload reports
INGEST_TOKEN = env("TOKEN")

TEMPLATES_DIR = os.path.join(SERVER_DIR, "app", "ui", "templates")
STATIC_DIR = os.path.join(SERVER_DIR, "app", "ui", "static")
