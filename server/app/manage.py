"""
Server administration.

    python -m app.manage init-db              create the tables (the server also does this on start)
    python -m app.manage worker [-n 2]        run background workers without the web server
    python -m app.manage process              run every queued job once, then exit
    python -m app.manage import-files [--docs DIR] [--database DIR]
                                              copy data of the file-based layout (before the database) in
    python -m app.manage create-user --email E [--name N] [--role admin|viewer]
                                              add a user; prints a temporary password to change at first sign-in
    python -m app.manage reset-password --email E
                                              new temporary password (e.g. a locked-out admin)
    python -m app.manage list-users

The database is MERGECLEAR_DATABASE_URL (or the SQLite default), as for the server.
"""
import argparse
import json
import logging
import os
import signal
import sys
import threading

from sqlalchemy import insert, select

from app import db, jobs
from app.config import DATABASE_DIR, DOCS_DIR

logger = logging.getLogger("mergeclear.manage")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("skipped %s: %s", path, e)
        return None


def _dirs(path):
    if not os.path.isdir(path):
        return []
    return sorted(d for d in os.listdir(path) if not d.startswith(".") and os.path.isdir(os.path.join(path, d)))


def import_files(docs_dir: str, database_dir: str) -> dict:
    """Idempotent: versions, plans and templates already in the database are kept."""
    from app.services import docs_store, test_templates
    from app.services.architecture_store import has_architecture, save_architecture
    from app.services.qa_plan_service import QAPlanService

    counts = {"versions": 0, "titles": 0, "architectures": 0, "qa_plans": 0, "templates": 0, "skipped": 0}
    versions_table = db.api_versions

    for repo in _dirs(docs_dir):
        if not docs_store.is_safe_name(repo):
            continue
        titles = _read_json(os.path.join(docs_dir, repo, ".titles.json")) or {}
        for api in _dirs(os.path.join(docs_dir, repo)):
            meta = {e.get("version"): e for e in (_read_json(os.path.join(database_dir, repo, f"{api}.json")) or [])
                    if isinstance(e, dict)}
            files = [f for f in os.listdir(os.path.join(docs_dir, repo, api)) if f.startswith("v") and f.endswith(".md")]
            for name in sorted(files, key=lambda f: int(f[1:-3]) if f[1:-3].isdigit() else -1):
                if not name[1:-3].isdigit():
                    continue
                version = int(name[1:-3])
                with open(os.path.join(docs_dir, repo, api, name), encoding="utf-8") as f:
                    content = f.read()
                entry = meta.get(version) or {}
                with db.engine().begin() as conn:
                    exists = conn.execute(select(versions_table.c.id).where(
                        versions_table.c.repo == repo, versions_table.c.api == api, versions_table.c.version == version)).first()
                    if exists:
                        counts["skipped"] += 1
                        continue
                    conn.execute(insert(versions_table).values(
                        repo=repo, api=api, version=version, content=content,
                        signature=entry.get("signature") or f"imported-v{version}",
                        commit=entry.get("commit_hash"), title=entry.get("title"), route=entry.get("route"),
                        created_at=db.utcnow(),
                    ))
                counts["versions"] += 1
        for api, entry in titles.items():
            if isinstance(entry, dict) and entry.get("title"):
                docs_store.set_title(repo, api, entry["title"], entry.get("source", "fallback"))
                counts["titles"] += 1

    architecture_dir = os.path.join(database_dir, "architecture")
    for repo in _dirs(architecture_dir):
        document = _read_json(os.path.join(architecture_dir, repo, "latest.json"))
        if document and not has_architecture(repo):
            save_architecture(repo, document.get("commit"), document.get("architecture"))
            counts["architectures"] += 1

    plans = QAPlanService()
    plans_dir = os.path.join(database_dir, "qa_plans")
    for repo in _dirs(plans_dir):
        for name in os.listdir(os.path.join(plans_dir, repo)):
            api, sep, version = name[:-5].rpartition("_v") if name.endswith(".json") else ("", "", "")
            if not sep or not version.isdigit():
                continue
            data = _read_json(os.path.join(plans_dir, repo, name))
            if data and not plans.plan_exists(repo, api, int(version)):
                plans.save_qa_plan(repo, api, int(version), data.get("plan") or {}, data.get("api_doc_hash"))
                counts["qa_plans"] += 1

    templates_dir = os.path.join(database_dir, "test_templates")
    for repo in _dirs(templates_dir):
        for name in os.listdir(os.path.join(templates_dir, repo)):
            data = _read_json(os.path.join(templates_dir, repo, name)) if name.endswith(".json") else None
            if data and data.get("name") and not test_templates.get_template(repo, data["name"]):
                test_templates.create_template(repo, data["name"], data.get("category"), data.get("description"),
                                               data.get("test_cases") or [])
                counts["templates"] += 1

    return counts


def run_workers(count: int):
    jobs.start_workers(count)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("%d worker(s) on %s; Ctrl+C to stop", count, db.describe())
    stop.wait()
    logger.info("stopping: finishing running jobs")
    jobs.stop_workers(timeout=60)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the tables")
    worker = sub.add_parser("worker", help="run background workers")
    worker.add_argument("-n", "--count", type=int, default=1)
    sub.add_parser("process", help="run queued jobs once, then exit")
    create = sub.add_parser("create-user", help="add a user")
    create.add_argument("--email", required=True)
    create.add_argument("--name", default="")
    create.add_argument("--role", choices=("admin", "viewer"), default="viewer")
    reset = sub.add_parser("reset-password", help="issue a new temporary password")
    reset.add_argument("--email", required=True)
    sub.add_parser("list-users", help="list users")
    imp = sub.add_parser("import-files", help="import the file-based layout")
    imp.add_argument("--docs", default=DOCS_DIR, help=f"docs folder (default: {DOCS_DIR})")
    imp.add_argument("--database", default=DATABASE_DIR, help=f"database folder (default: {DATABASE_DIR})")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    db.engine()
    if args.command == "init-db":
        print(f"tables ready in {db.describe()}")
    elif args.command == "worker":
        run_workers(max(1, args.count))
    elif args.command == "process":
        print(f"ran {jobs.run_pending('manage')} job(s)")
    elif args.command in ("create-user", "reset-password", "list-users"):
        from app import auth
        try:
            if args.command == "create-user":
                password = auth.generate_password()
                user = auth.create_user(args.email, args.name, args.role, password, must_change_password=True)
                print(f"created {user['role']} {user['email']}; temporary password (changed at first sign-in): {password}")
            elif args.command == "reset-password":
                user = auth.get_user_by_email(args.email)
                if user is None:
                    raise auth.AuthError(f"no user {args.email}")
                password = auth.generate_password()
                auth.set_password(user["id"], password, must_change=True)
                if not user["active"]:
                    auth.update_user(user["id"], active=True)
                print(f"temporary password for {user['email']} (changed at first sign-in): {password}")
            else:
                for u in auth.list_users():
                    print(f"{u['email']:<40} {u['role']:<7} {'active' if u['active'] else 'deactivated'}")
        except auth.AuthError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    elif args.command == "import-files":
        counts = import_files(args.docs, args.database)
        print(f"imported into {db.describe()}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
