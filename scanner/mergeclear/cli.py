"""
mergeclear command line.

Works the same on a laptop, in any CI system, in a git hook or a cron job:
it only reads the file system and git, and reports through files, stdout and
exit codes.

    mergeclear scan [PATH] [--commit SHA | --since REF] [--out FILE] [--push URL]
    mergeclear push REPORT [--server URL]
    mergeclear diff BASE HEAD [--format text|markdown|json]
    mergeclear check [HEAD] (--against BASE | --base REF) [--fail-on hold|review]

Exit codes: 0 ok / clear, 1 the check failed (hold, or review with
--fail-on review), 2 usage or runtime error.

Settings are read from flags, then environment variables (MERGECLEAR_SERVER,
MERGECLEAR_API_KEY), then a mergeclear.yml in the scanned folder.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

from mergeclear import __version__
from mergeclear.contracts import CLEAR, HOLD, REVIEW, VERDICT_ORDER, compare_reports

EXIT_OK, EXIT_FAILED, EXIT_ERROR = 0, 1, 2
CONFIG_FILES = ("mergeclear.yml", "mergeclear.yaml", ".mergeclear.yml")

logger = logging.getLogger("mergeclear.cli")


class CliError(Exception):
    """A problem the user can fix; printed without a traceback."""


# ---------------------------------------------------------------- settings

def load_config(path: str) -> dict:
    for name in CONFIG_FILES:
        file = os.path.join(path, name)
        if os.path.isfile(file):
            import yaml
            with open(file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise CliError(f"{file}: expected key: value pairs")
            return data
    return {}


def setting(flag_value, env_name, config: dict, key: str, default=None):
    if flag_value not in (None, ""):
        return flag_value
    if os.environ.get(env_name):
        return os.environ[env_name]
    return config.get(key, default)


def api_key():
    """MERGECLEAR_API_KEY (created under Settings → API keys); MERGECLEAR_TOKEN is the older name."""
    return os.environ.get("MERGECLEAR_API_KEY") or os.environ.get("MERGECLEAR_TOKEN") or None


# ---------------------------------------------------------------- git facts

def _git(path, *args):
    try:
        out = subprocess.check_output(["git", *args], cwd=path, stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def repo_facts(path: str) -> dict:
    """Commit, branch and name of the checkout; None where git cannot tell (or there is no git)."""
    commit = _git(path, "rev-parse", "HEAD")
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":          # detached checkout, typical in CI: pass --branch
        branch = None
    remote = _git(path, "config", "--get", "remote.origin.url")
    top = _git(path, "rev-parse", "--show-toplevel")
    name = None
    # the remote names the whole repository: only use it when scanning its root, not a subfolder
    if remote and top and os.path.realpath(top) == os.path.realpath(path):
        name = remote.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        name = name[:-4] if name.endswith(".git") else name
    dirty = bool(_git(path, "status", "--porcelain")) if commit else False
    return {"commit": commit, "branch": branch, "name": name, "dirty": dirty}


# ---------------------------------------------------------------- reports

def read_report(file: str) -> dict:
    try:
        with open(file, encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        raise CliError(f"{file}: no such file")
    except json.JSONDecodeError as e:
        raise CliError(f"{file}: not a mergeclear report ({e})")
    if not isinstance(report, dict) or "routes" not in report:
        raise CliError(f"{file}: not a mergeclear report (no routes)")
    return report


def write_json(data, file):
    text = json.dumps(data, indent=2)
    if file in (None, "-"):
        sys.stdout.write(text + "\n")
    else:
        with open(file, "w", encoding="utf-8") as f:
            f.write(text + "\n")


def push_report(report: dict, server: str, token: str = None) -> dict:
    import requests

    if not server:
        raise CliError("no server: pass --server, set MERGECLEAR_SERVER or add `server:` to mergeclear.yml")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = server.rstrip("/") + "/analyze"
    try:
        response = requests.post(url, json=report, headers=headers, timeout=120)
    except requests.RequestException as e:
        raise CliError(f"could not reach {url}: {e}")
    if response.status_code == 401:
        raise CliError(f"{url} needs an API key (401): create one under Settings → API keys on the server "
                       "and set MERGECLEAR_API_KEY")
    if response.status_code == 403:
        raise CliError(f"{url} refused the upload (403): the API key must belong to an admin")
    if response.status_code == 503:
        raise CliError(f"{url} is not set up yet (503): open it in a browser and create the admin account")
    if not response.ok:
        raise CliError(f"{url} answered {response.status_code}: {response.text[:300]}")
    return response.json() if response.content else {}


def wait_for_job(server: str, answer: dict, token: str = None, timeout: float = 600, poll: float = 2.0,
                 sleep=None) -> dict:
    """Poll the server until the queued upload is documented. Raises CliError when it fails or times out."""
    import time

    import requests

    sleep = sleep or time.sleep
    job_url = answer.get("job_url")
    if not job_url:
        raise CliError("the server did not return a job to wait for (older server?)")
    url = server.rstrip("/") + job_url
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            job = requests.get(url, headers=headers, timeout=30).json()
        except (requests.RequestException, ValueError) as e:
            raise CliError(f"could not read {url}: {e}")
        state = (job.get("status"), job.get("progress_done"), job.get("progress_total"))
        if state != last:
            last = state
            if job.get("progress_total"):
                logger.info("job %s: %s, %s of %s endpoints", job.get("id"), job.get("status"),
                            job.get("progress_done"), job.get("progress_total"))
            else:
                logger.info("job %s: %s", job.get("id"), job.get("status"))
        if job.get("status") == "done":
            return job
        if job.get("status") == "failed":
            raise CliError(f"the server could not document the upload: {(job.get('error') or '').splitlines()[0] if job.get('error') else 'unknown error'}")
        if time.monotonic() > deadline:
            raise CliError(f"gave up waiting after {int(timeout)}s; the job continues on the server: {url}")
        sleep(poll)


def _report_done(job: dict):
    result = job.get("result") or {}
    logger.info("documented: %s new version(s), %s unchanged", result.get("created", 0), result.get("unchanged", 0))


# ---------------------------------------------------------------- formatting

ICONS = {CLEAR: "✅", REVIEW: "🟡", HOLD: "🔴"}
LABELS = {CLEAR: "Clear", REVIEW: "Review", HOLD: "Hold"}
EXPLAIN = {
    CLEAR: "No change existing clients could notice.",
    REVIEW: "Changes clients may notice; worth a look before merging.",
    HOLD: "Breaking changes: existing clients fail until they are updated.",
}


def _short(commit):
    return (commit or "?")[:12]


def format_text(result: dict) -> str:
    v = result["verdict"]
    s = result["summary"]
    lines = [f"{ICONS[v]} {LABELS[v]}: {EXPLAIN[v]}",
             f"   {_short(result['base']['commit'])} → {_short(result['head']['commit'])}: "
             f"{s['endpoints_changed']} endpoint(s) changed · {s['breaking']} breaking · {s['minor']} minor · {s['additive']} additive"]
    if not result["complete"]:
        lines.append("   note: a report is an incremental scan, removed endpoints are not detected")
    for e in result["endpoints"]:
        lines.append("")
        lines.append(f"{ICONS[e['verdict']]} {e['method']} {e['path']}  ({e['name']}, {e['status']})")
        for c in e["changes"]:
            lines.append(f"    {c['severity']:<9} {c['detail'].replace('`', '')}")
        for group, delta in (e.get("dependencies") or {}).items():
            for item in delta.get("added", []):
                lines.append(f"    {'new dep':<9} {group.replace('_', ' ')}: {item}")
            for item in delta.get("removed", []):
                lines.append(f"    {'dropped':<9} {group.replace('_', ' ')}: {item}")
    return "\n".join(lines)


def format_markdown(result: dict) -> str:
    """For pull/merge request comments in any system."""
    v = result["verdict"]
    s = result["summary"]
    out = [f"### {ICONS[v]} Mergeclear: {LABELS[v]}", "", EXPLAIN[v], "",
           f"`{_short(result['base']['commit'])}` → `{_short(result['head']['commit'])}` · "
           f"{s['endpoints_changed']} endpoint(s) changed · **{s['breaking']} breaking** · {s['minor']} minor · {s['additive']} additive"]
    for e in result["endpoints"]:
        out += ["", f"#### {ICONS[e['verdict']]} `{e['method']} {e['path']}`", ""]
        for c in e["changes"]:
            out.append(f"- **{c['severity']}**: {c['detail']}")
        for group, delta in (e.get("dependencies") or {}).items():
            for item in delta.get("added", []):
                out.append(f"- **new dependency** ({group.replace('_', ' ')}): `{item}`")
            for item in delta.get("removed", []):
                out.append(f"- **dropped dependency** ({group.replace('_', ' ')}): `{item}`")
    if not result["complete"]:
        out += ["", "_One report is an incremental scan, so removed endpoints are not detected._"]
    return "\n".join(out)


def emit(result: dict, fmt: str, out: str = None):
    if fmt == "json":
        write_json(result, out)
        return
    text = format_markdown(result) if fmt == "markdown" else format_text(result)
    if out in (None, "-"):
        print(text)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")


# ---------------------------------------------------------------- commands

def build_report(path, name=None, branch=None, commit=None, since=None, config=None) -> dict:
    """Scan a folder into a report with repository facts (name, commit, branch)."""
    from mergeclear.core.scanner import Scanner

    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise CliError(f"{path}: not a directory")
    config = load_config(path) if config is None else config
    facts = repo_facts(path)
    label = commit or facts["commit"] or datetime.now(timezone.utc).strftime("local-%Y%m%d-%H%M%S")

    scanner = Scanner()
    if commit:
        if not facts["commit"]:
            raise CliError("--commit needs a git checkout")
        report = scanner.scan(path, commit)
    elif since:
        if not facts["commit"]:
            raise CliError("--since needs a git checkout")
        report = scanner.scan_since(path, since, label=label)
    else:
        report = scanner.scan_full(path, label=label)

    report["repository"] = setting(name, "MERGECLEAR_NAME", config, "name") or facts["name"] or report["repository"]
    report["commit"] = label
    report["branch"] = setting(branch, "MERGECLEAR_BRANCH", config, "branch") or facts["branch"]
    report["dirty"] = facts["dirty"]
    report["scanned_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["scanner"] = {"name": "mergeclear", "version": __version__}

    logger.info("%s @ %s: %d endpoint(s), frameworks: %s, mode: %s", report["repository"], _short(label),
                len(report.get("routes") or []), ", ".join(report.get("frameworks") or []) or "none", report["scan_mode"])
    if not report.get("frameworks"):
        logger.warning("no supported framework found in %s", path)
    return report


def scan_ref(path: str, ref: str, name: str) -> dict:
    """Scan `path` as it is at git `ref`, in a temporary worktree (the checkout is not touched)."""
    import tempfile

    path = os.path.abspath(path)
    top = _git(path, "rev-parse", "--show-toplevel")
    if not top:
        raise CliError("--base needs a git checkout")
    commit = _git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if not commit:
        raise CliError(f"--base {ref}: unknown git ref (in CI, fetch it first: git fetch origin <branch>)")
    relative = os.path.relpath(os.path.realpath(path), os.path.realpath(top))

    with tempfile.TemporaryDirectory(prefix="mergeclear-base-") as tmp:
        worktree = os.path.join(tmp, "base")
        try:
            subprocess.run(["git", "worktree", "add", "--detach", "--quiet", worktree, commit],
                           cwd=top, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise CliError(f"could not check out {ref}: {e.stderr.decode().strip()}")
        try:
            report = build_report(os.path.join(worktree, relative), name=name, commit=None, config={})
            report["commit"] = commit
            report["branch"] = ref
            return report
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", worktree], cwd=top, capture_output=True)


def cmd_scan(args) -> int:
    config = load_config(os.path.abspath(args.path)) if os.path.isdir(args.path) else {}
    report = build_report(args.path, name=args.name, branch=args.branch, commit=args.commit, since=args.since, config=config)

    if args.out or args.push is None:
        write_json(report, args.out)
    if args.push is not None:
        server = setting(args.push, "MERGECLEAR_SERVER", config, "server")
        token = api_key()
        answer = push_report(report, server, token)
        logger.info("sent to %s (%s)", server, answer.get("job_url") or answer.get("status", "ok"))
        if args.wait:
            _report_done(wait_for_job(server, answer, token, timeout=args.wait))
    elif args.wait:
        raise CliError("--wait needs --push")
    return EXIT_OK


def cmd_push(args) -> int:
    report = read_report(args.report)
    config = load_config(os.getcwd())
    server = setting(args.server, "MERGECLEAR_SERVER", config, "server")
    token = api_key()
    answer = push_report(report, server, token)
    logger.info("sent %s @ %s to %s (%s)", report.get("repository"), _short(report.get("commit")), server,
                answer.get("job_url") or answer.get("status", "ok"))
    if args.wait:
        _report_done(wait_for_job(server, answer, token, timeout=args.wait))
    return EXIT_OK


def cmd_diff(args) -> int:
    result = compare_reports(read_report(args.base), read_report(args.head))
    emit(result, args.format, args.out)
    return EXIT_OK


def cmd_check(args) -> int:
    # head: a report file, or a folder to scan now (default: the current folder)
    if os.path.isdir(args.head):
        head = build_report(args.head, name=args.name)
    else:
        head = read_report(args.head)
    if args.base:
        if not os.path.isdir(args.head):
            raise CliError("--base needs HEAD to be a folder (the checkout to compare)")
        base = scan_ref(args.head, args.base, head["repository"])
    else:
        base = read_report(args.against)
    result = compare_reports(base, head)
    emit(result, args.format, args.out)
    threshold = HOLD if args.fail_on == "hold" else REVIEW
    if args.fail_on != "never" and VERDICT_ORDER[result["verdict"]] >= VERDICT_ORDER[threshold]:
        return EXIT_FAILED
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mergeclear",
        description="Know what a change breaks before you merge it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 ok/clear · 1 check failed · 2 error. Run `mergeclear <command> -h` for details.",
    )
    parser.add_argument("--version", action="version", version=f"mergeclear {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logs on stderr")
    parser.add_argument("-q", "--quiet", action="store_true", help="only errors on stderr")
    sub = parser.add_subparsers(dest="command", metavar="command")

    scan = sub.add_parser("scan", help="scan a repository into a report",
                          description="Scan a folder into a report (JSON). Without --commit/--since every endpoint is included.")
    scan.add_argument("path", nargs="?", default=".", help="folder to scan (default: current)")
    mode = scan.add_mutually_exclusive_group()
    mode.add_argument("--commit", help="only endpoints touched by this commit")
    mode.add_argument("--since", metavar="REF", help="only endpoints touched since REF (e.g. origin/main)")
    scan.add_argument("--out", "-o", metavar="FILE", help="write the report here (default: stdout unless --push)")
    scan.add_argument("--push", nargs="?", const="", metavar="URL",
                      help="send the report to a Mergeclear server (URL, or MERGECLEAR_SERVER / mergeclear.yml)")
    scan.add_argument("--name", help="repository name (default: git remote or folder name)")
    scan.add_argument("--branch", help="branch name, for detached CI checkouts")
    scan.add_argument("--wait", nargs="?", const=600, type=float, metavar="SECONDS", help="after pushing, wait until the server has documented the upload (default limit 600 s)")
    scan.set_defaults(run=cmd_scan)

    push = sub.add_parser("push", help="send a report to a Mergeclear server")
    push.add_argument("report", help="report file written by `mergeclear scan --out`")
    push.add_argument("--server", help="server URL (default: MERGECLEAR_SERVER or mergeclear.yml)")
    push.add_argument("--wait", nargs="?", const=600, type=float, metavar="SECONDS", help="after pushing, wait until the server has documented the upload (default limit 600 s)")
    push.set_defaults(run=cmd_push)

    for name, helptext in (("diff", "compare two reports"), ("check", "compare two reports and fail on breaking changes")):
        p = sub.add_parser(name, help=helptext)
        if name == "diff":
            p.add_argument("base", help="report of the base (e.g. main)")
            p.add_argument("head", help="report of the change")
        else:
            p.add_argument("head", nargs="?", default=".", help="report of the change, or a folder to scan (default: .)")
            against = p.add_mutually_exclusive_group(required=True)
            against.add_argument("--against", metavar="REPORT", help="report of the base")
            against.add_argument("--base", metavar="REF", help="git ref to compare with, scanned in a temporary worktree (e.g. origin/main)")
            p.add_argument("--name", help="repository name when scanning (default: git remote or folder name)")
            p.add_argument("--fail-on", choices=("hold", "review", "never"), default="hold",
                           help="exit 1 at this verdict or worse (default: hold)")
        p.add_argument("--format", "-f", choices=("text", "markdown", "json"), default="text")
        p.add_argument("--out", "-o", metavar="FILE", help="write the result here (default: stdout)")
        p.set_defaults(run=cmd_diff if name == "diff" else cmd_check)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "run", None):
        parser.print_help()
        return EXIT_ERROR

    # logs go to stderr so stdout stays clean JSON / markdown
    # (the scanner's own progress logs only with -v)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="mergeclear: %(message)s" if not args.verbose else "[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if not args.verbose:
        logger.setLevel(logging.ERROR if args.quiet else logging.INFO)
    try:
        return args.run(args)
    except CliError as e:
        print(f"mergeclear: error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
