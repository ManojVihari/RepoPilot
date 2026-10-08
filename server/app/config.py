"""Server-wide settings. Paths are absolute so the server works from any cwd."""
import os

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOCS_DIR = os.path.join(SERVER_DIR, "docs")
DATABASE_DIR = os.path.join(SERVER_DIR, "database")
SQLITE_PATH = os.path.join(SERVER_DIR, "docai.db")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral")

TEMPLATES_DIR = os.path.join(SERVER_DIR, "app", "ui", "templates")
STATIC_DIR = os.path.join(SERVER_DIR, "app", "ui", "static")
