#!/usr/bin/env python3
"""
Generate complete DocAI documentation for a local repository (local testing).

Scans every endpoint of the folder - no git history or commit needed - and
writes the docs, versions and architecture model exactly like the server's
/analyze would, then tells you where to look.

    # docs + architecture into server/docs and server/database
    python scripts/document_local_repo.py ~/code/my-service

    # without Ollama, into a throwaway folder, starting clean
    python scripts/document_local_repo.py ~/code/my-service --no-llm --data-dir /tmp/docai --fresh

    # send to a running server instead of generating in-process
    python scripts/document_local_repo.py ~/code/my-service --server http://localhost:8000

Then view it with:  cd server && python run.py   ->  http://localhost:8000/ui
(with --data-dir, start the server with the same DOCAI_DOCS_DIR / DOCAI_DATABASE_DIR)
"""
import argparse
import json
import logging
import os
import shutil
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="Path to the local repository / project folder")
    parser.add_argument("--name", help="Repository name in DocAI (default: folder name)")
    parser.add_argument("--label", help="Version label recorded as the commit (default: local-<timestamp>)")
    parser.add_argument("--no-llm", action="store_true", help="Do not call Ollama; build docs from scanner data only")
    parser.add_argument("--data-dir", help="Write docs/ and database/ under this folder instead of server/")
    parser.add_argument("--fresh", action="store_true", help="Delete existing docs/versions/architecture of this repo first")
    parser.add_argument("--server", help="POST the scan to a running DocAI server instead of generating in-process")
    parser.add_argument("--save-json", help="Also write the raw scanner output to this file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logs")
    return parser.parse_args()


# import name -> pip package, for a clear message before anything runs
REQUIREMENTS = {
    "tree_sitter": "tree-sitter>=0.25",
    "tree_sitter_java": "tree-sitter-java",
    "tree_sitter_python": "tree-sitter-python",
    "yaml": "pyyaml",
    "requests": "requests",
    "fastapi": "fastapi",
    "pydantic": "pydantic>=2",
    "jinja2": "jinja2",
    "markdown": "markdown",
    "bs4": "beautifulsoup4",
}


def check_requirements():
    import importlib.util

    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ is required (tree-sitter 0.25), this is {sys.version.split()[0]} at {sys.executable}")

    missing = [pkg for module, pkg in REQUIREMENTS.items() if importlib.util.find_spec(module) is None]
    if missing:
        sys.exit(
            f"Missing Python packages for {sys.executable}:\n  " + "\n  ".join(missing)
            + "\n\nInstall them with:\n"
            + f"  {sys.executable} -m pip install -r scanner/requirements.txt -r server/requirements.txt"
        )

    from tree_sitter import Query  # noqa: F401  (0.25+ API used by the extractors)


def main():
    args = parse_args()
    check_requirements()

    repo_path = os.path.abspath(os.path.expanduser(args.repo))
    if not os.path.isdir(repo_path):
        sys.exit(f"Not a directory: {repo_path}")

    name = args.name or os.path.basename(repo_path.rstrip(os.sep))
    label = args.label or time.strftime("local-%Y%m%d-%H%M%S")

    # Environment must be set before the server modules are imported
    if args.data_dir:
        data_dir = os.path.abspath(args.data_dir)
        os.environ["DOCAI_DOCS_DIR"] = os.path.join(data_dir, "docs")
        os.environ["DOCAI_DATABASE_DIR"] = os.path.join(data_dir, "database")
    if args.no_llm:
        os.environ["DOCAI_LLM"] = "off"

    sys.path[:0] = [os.path.join(ROOT, "scanner"), os.path.join(ROOT, "server")]

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    # ---------- scan ----------
    from docai.core.scanner import Scanner

    started = time.time()
    result = Scanner().scan_full(repo_path, label=label)
    result["repository"] = name
    scan_seconds = time.time() - started

    routes = result.get("routes", [])
    if args.save_json:
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"Scanner output written to {args.save_json}")

    if not routes:
        print(f"\nNo endpoints found in {repo_path} (frameworks detected: {result.get('frameworks') or 'none'}).")
        return 1

    print(f"\nScanned {repo_path} in {scan_seconds:.1f}s: {len(routes)} endpoint(s), "
          f"frameworks: {', '.join(result.get('frameworks') or [])}")

    # ---------- send to a running server ----------
    if args.server:
        import requests

        response = requests.post(f"{args.server.rstrip('/')}/analyze", json=result, timeout=120)
        response.raise_for_status()
        print(f"Sent to {args.server} ({response.json().get('status')}); docs are generated in the background.")
        print(f"View: {args.server.rstrip('/')}/ui/{name}"
              + (f"  ·  {args.server.rstrip('/')}/ui/{name}/architecture" if result.get("architecture") else ""))
        return 0

    # ---------- generate in-process ----------
    from app.config import DATABASE_DIR, DOCS_DIR, LLM_ENABLED
    from app.models.schema import AnalyzeRequest
    from app.services import docs_store
    from app.services.architecture_store import ARCHITECTURE_DIR, save_architecture
    from app.services.doc_service import doc_name, process_routes

    if not docs_store.is_safe_name(name):
        sys.exit(f"Invalid repository name: {name!r} (use --name)")

    if args.fresh:
        for path in (
            os.path.join(DOCS_DIR, name),
            os.path.join(DATABASE_DIR, name),
            os.path.join(DATABASE_DIR, "qa_plans", name),
            os.path.join(ARCHITECTURE_DIR, name),
        ):
            if os.path.isdir(path):
                shutil.rmtree(path)
        print(f"Removed previous data of '{name}'")

    request = AnalyzeRequest(**result)

    names = [doc_name(r) for r in request.routes]
    duplicates = sorted(n for n, count in Counter(names).items() if count > 1)
    before = {n: docs_store.list_versions(name, n) for n in set(names)}

    if not LLM_ENABLED:
        print("LLM disabled: docs are built from scanner data only")
    else:
        print("Generating docs with Ollama (use --no-llm to skip)...")

    started = time.time()
    process_routes(request.routes, request.commit, name)
    if request.architecture:
        save_architecture(name, request.commit, request.architecture)

    created = sum(1 for n in set(names) if docs_store.list_versions(name, n) != before[n])
    unchanged = len(set(names)) - created

    print("\n" + "=" * 60)
    print(f"Repository   : {name}")
    print(f"Endpoints    : {len(routes)}")
    print(f"Documented   : {created} new version(s), {unchanged} unchanged (same contract as last run)")
    if duplicates:
        print(f"Note         : {len(duplicates)} name(s) shared by several endpoints (overloads), last one wins: "
              + ", ".join(duplicates[:5]) + (" ..." if len(duplicates) > 5 else ""))
    print(f"Docs folder  : {os.path.join(DOCS_DIR, name)}")
    if request.architecture:
        print(f"Architecture : {os.path.join(ARCHITECTURE_DIR, name, 'latest.json')}")
    print(f"Took         : {time.time() - started:.1f}s")
    print("=" * 60)

    env = ""
    if args.data_dir:
        env = f"DOCAI_DOCS_DIR={DOCS_DIR} DOCAI_DATABASE_DIR={DATABASE_DIR} "
    print("\nView it:")
    print(f"  cd {os.path.join(ROOT, 'server')} && {env}python run.py")
    print(f"  http://localhost:8000/ui/{name}")
    if request.architecture:
        print(f"  http://localhost:8000/ui/{name}/architecture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
