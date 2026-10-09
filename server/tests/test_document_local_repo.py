import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "document_local_repo.py"
FIXTURE = ROOT / "scanner" / "tests" / "fixtures" / "spring_shop"


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURE), "--no-llm", *args],
        capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONPATH": ""},
    )


def test_documents_a_local_folder_without_git(tmp_path):
    result = run("--data-dir", str(tmp_path), "--name", "shop", "--fresh", "--save-json", str(tmp_path / "scan.json"))
    assert result.returncode == 0, result.stderr

    assert "Documented   : 4 new version(s), 0 unchanged" in result.stdout

    from app import db
    from app.services import docs_store
    from app.services.architecture_store import load_architecture
    db.use(f"sqlite:///{tmp_path / 'mergeclear.db'}")

    assert docs_store.list_apis("shop") == [
        "OrderController.cancel", "OrderController.create", "OrderController.get", "OrderController.list",
    ]
    doc = docs_store.read_version("shop", "OrderController.create", 1)
    assert "## Dependencies & Integrations" in doc
    assert "Places a new order." in doc   # Javadoc used as overview without an LLM
    assert doc.startswith("# Places a new order\n")   # display title from the Javadoc
    assert "- **API id:** `OrderController.create`" in doc
    assert docs_store.title_entry("shop", "OrderController.create") == {"title": "Places a new order", "source": "fallback"}
    assert docs_store.get_title("shop", "OrderController.list") == "List (Order)"

    assert load_architecture("shop")["commit"].startswith("local-")
    assert json.loads((tmp_path / "scan.json").read_text())["routes"]

    # same code again: nothing new to document
    again = run("--data-dir", str(tmp_path), "--name", "shop")
    assert again.returncode == 0, again.stderr
    assert "Documented   : 0 new version(s), 4 unchanged" in again.stdout
