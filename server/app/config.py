"""Server-wide settings. Paths are absolute so the server works from any cwd."""
import os

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DOCAI_DOCS_DIR / DOCAI_DATABASE_DIR let local test runs write elsewhere
DOCS_DIR = os.environ.get("DOCAI_DOCS_DIR") or os.path.join(SERVER_DIR, "docs")
DATABASE_DIR = os.environ.get("DOCAI_DATABASE_DIR") or os.path.join(SERVER_DIR, "database")
SQLITE_PATH = os.path.join(SERVER_DIR, "docai.db")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral")

# DOCAI_LLM=off generates docs from scanner data only (no Ollama calls)
LLM_ENABLED = os.environ.get("DOCAI_LLM", "on").lower() not in ("off", "0", "false", "no")

TEMPLATES_DIR = os.path.join(SERVER_DIR, "app", "ui", "templates")
STATIC_DIR = os.path.join(SERVER_DIR, "app", "ui", "static")
