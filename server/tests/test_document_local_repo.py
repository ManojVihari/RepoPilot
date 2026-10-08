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
    docs = tmp_path / "docs" / "shop"
    assert sorted(p.name for p in docs.iterdir()) == [
        "OrderController.cancel", "OrderController.create", "OrderController.get", "OrderController.list",
    ]
    doc = (docs / "OrderController.create" / "v1.md").read_text()
    assert "## Dependencies & Integrations" in doc
    assert "Places a new order." in doc   # Javadoc used as overview without an LLM

    stored = json.loads((tmp_path / "database" / "architecture" / "shop" / "latest.json").read_text())
    assert stored["commit"].startswith("local-")
    assert json.loads((tmp_path / "scan.json").read_text())["routes"]

    # same code again: nothing new to document
    again = run("--data-dir", str(tmp_path), "--name", "shop")
    assert again.returncode == 0, again.stderr
    assert "Documented   : 0 new version(s), 4 unchanged" in again.stdout
