"""Server-wide settings, from environment variables. Paths are absolute so the server works from any cwd."""
import os

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def env(name, default=None):
    """MERGECLEAR_<name>; the pre-rename DOCAI_<name> still works for existing setups."""
    return os.environ.get(f"MERGECLEAR_{name}") or os.environ.get(f"DOCAI_{name}") or default


# Postgres in production, e.g. postgresql+psycopg://mergeclear:secret@postgres:5432/mergeclear.
# Unset: a SQLite file in DATABASE_DIR (local runs and tests).
DATABASE_URL = env("DATABASE_URL")
DATABASE_DIR = env("DATABASE_DIR") or os.path.join(SERVER_DIR, "database")
# only read by `python -m app.manage import-files` (data of versions before the database)
DOCS_DIR = env("DOCS_DIR") or os.path.join(SERVER_DIR, "docs")

# background workers in the server process (0: run `python -m app.manage worker` separately)
WORKERS = int(env("WORKERS", "1"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral")

# MERGECLEAR_LLM=off generates docs from scanner data only (no Ollama calls)
LLM_ENABLED = (env("LLM", "on") or "on").lower() not in ("off", "0", "false", "no")

# ---- accounts
# "true": people can create their own viewer account on the sign-in page (default: admins add users)
ALLOW_SIGNUP = (env("ALLOW_SIGNUP", "false") or "").lower() in ("1", "true", "yes", "on")
# how long a sign-in lasts
SESSION_DAYS = float(env("SESSION_DAYS", "7"))
# Secure flag on the session cookie: "auto" (when the request is https), "true" (behind a TLS proxy) or "false"
COOKIE_SECURE = (env("COOKIE_SECURE", "auto") or "auto").lower()

# ---- limits (a misconfigured pipeline must not take the server down)
# largest request body accepted, e.g. a scan report (0: no limit)
MAX_UPLOAD_BYTES = int(float(env("MAX_UPLOAD_MB", "25")) * 1_000_000)
# uploads (/analyze) per account per minute, counted per server process (0: no limit)
UPLOADS_PER_MINUTE = int(env("UPLOADS_PER_MINUTE", "30"))

TEMPLATES_DIR = os.path.join(SERVER_DIR, "app", "ui", "templates")
STATIC_DIR = os.path.join(SERVER_DIR, "app", "ui", "static")
