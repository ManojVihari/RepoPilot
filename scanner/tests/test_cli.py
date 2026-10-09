import json
import subprocess
import textwrap

import pytest

from mergeclear import cli

CONTROLLER = """
@RestController
@RequestMapping("/orders")
public class OrderController {
    @PostMapping
    public OrderResponse create(@Valid @RequestBody OrderRequest request) { return null; }

    @GetMapping("/{id}")
    public OrderResponse get(@PathVariable Long id) { return null; }
}
"""
REQUEST = """
public class OrderRequest {
    @NotNull
    private String customer;
}
"""
RESPONSE = """
public class OrderResponse {
    private Long id;
    private String status;
}
"""


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def write(repo, path, content):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content))


@pytest.fixture
def shop(tmp_path):
    repo = tmp_path / "shop"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    write(repo, "pom.xml", "<project><dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>")
    write(repo, "src/main/java/shop/OrderController.java", CONTROLLER)
    write(repo, "src/main/java/shop/OrderRequest.java", REQUEST)
    write(repo, "src/main/java/shop/OrderResponse.java", RESPONSE)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def run(*argv):
    return cli.main([*map(str, argv)])


def scan(repo, out, *extra):
    assert run("-q", "scan", repo, "--out", out, *extra) == 0
    return json.loads(out.read_text())


def test_scan_writes_a_self_describing_report(shop, tmp_path):
    report = scan(shop, tmp_path / "r.json")
    assert report["repository"] == "shop"
    assert report["branch"] == "main"
    assert report["scan_mode"] == "full"
    assert report["scanner"]["name"] == "mergeclear"
    assert len(report["commit"]) == 40 and report["dirty"] is False
    assert {(r["method"], r["path"]) for r in report["routes"]} == {("POST", "/orders"), ("GET", "/orders/{id}")}


def test_check_holds_breaking_changes(shop, tmp_path, capsys):
    base = tmp_path / "base.json"
    scan(shop, base)

    git(shop, "checkout", "-qb", "feature")
    write(shop, "src/main/java/shop/OrderRequest.java", REQUEST.replace("}", "    @NotNull\n    private String warehouse;\n}", 1))
    write(shop, "src/main/java/shop/OrderResponse.java", RESPONSE.replace("    private String status;\n", ""))
    git(shop, "commit", "-qam", "breaking")
    head = tmp_path / "head.json"
    assert scan(shop, head)["branch"] == "feature"
    capsys.readouterr()

    assert run("check", head, "--against", base) == cli.EXIT_FAILED
    text = capsys.readouterr().out
    assert text.startswith("🔴 Hold")
    assert "warehouse" in text and "status" in text

    assert run("check", head, "--against", base, "--fail-on", "never", "--format", "markdown") == 0
    markdown = capsys.readouterr().out
    assert markdown.startswith("### 🔴 Mergeclear: Hold") and "**breaking**" in markdown

    assert run("diff", base, head, "--format", "json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "hold" and result["summary"]["breaking"] >= 2
    assert {e["path"] for e in result["endpoints"]} == {"/orders", "/orders/{id}"}


def test_check_clears_additive_changes(shop, tmp_path, capsys):
    base = tmp_path / "base.json"
    scan(shop, base)
    write(shop, "src/main/java/shop/OrderResponse.java", RESPONSE.replace("}", "    private String trackingUrl;\n}", 1))
    head = tmp_path / "head.json"
    assert scan(shop, head)["dirty"] is True            # uncommitted edits are scanned too
    capsys.readouterr()

    assert run("check", head, "--against", base, "--fail-on", "review") == 0
    assert capsys.readouterr().out.startswith("✅ Clear")


def test_removed_endpoints_need_full_scans(shop, tmp_path, capsys):
    base = tmp_path / "base.json"
    scan(shop, base)
    write(shop, "src/main/java/shop/OrderController.java", CONTROLLER.replace('@GetMapping("/{id}")', '@GetMapping("/by-id/{id}")'))
    git(shop, "commit", "-qam", "move get")

    head = tmp_path / "head.json"
    scan(shop, head)
    assert run("check", head, "--against", base) == cli.EXIT_FAILED

    # an incremental scan only knows what changed: compares, but cannot claim removals
    partial = tmp_path / "partial.json"
    report = scan(shop, partial, "--since", "HEAD~1")
    assert report["scan_mode"] == "changes"
    capsys.readouterr()
    run("diff", base, partial)
    assert "incremental scan" in capsys.readouterr().out


def test_push_sends_the_report_with_the_token(shop, tmp_path, monkeypatch, capsys):
    import requests

    sent = {}

    class Answer:
        status_code, ok, content = 200, True, b"{}"

        def json(self):
            return {"status": "processing"}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, report=json, headers=headers)
        return Answer()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("MERGECLEAR_API_KEY", "s3cret")
    (shop / "mergeclear.yml").write_text("server: http://mergeclear.internal/\nname: shop-api\n")

    assert run("-q", "scan", shop, "--push") == 0
    assert capsys.readouterr().out == ""                    # pushing does not also dump the report
    assert sent["url"] == "http://mergeclear.internal/analyze"
    assert sent["headers"] == {"Authorization": "Bearer s3cret"}
    assert sent["report"]["repository"] == "shop-api"
    assert sent["report"]["project"] == "shop-api"          # `name:` is the older spelling of `project:`

    out = tmp_path / "r.json"
    out.write_text(json.dumps(sent["report"]))
    assert run("-q", "push", out, "--server", "http://other:9000") == 0
    assert sent["url"] == "http://other:9000/analyze"


def test_errors_exit_2_with_a_message(tmp_path, capsys):
    assert run("diff", tmp_path / "missing.json", tmp_path / "missing.json") == cli.EXIT_ERROR
    assert "no such file" in capsys.readouterr().err
    (tmp_path / "bad.json").write_text("{}")
    assert run("push", tmp_path / "bad.json") == cli.EXIT_ERROR
    assert "not a mergeclear report" in capsys.readouterr().err
    assert run("scan", tmp_path, "--commit", "abc") == cli.EXIT_ERROR   # not a git checkout
    assert run() == cli.EXIT_ERROR


def test_repository_name_comes_from_the_remote_only_at_the_root(shop, tmp_path):
    git(shop, "remote", "add", "origin", "git@github.com:acme/shop-api.git")
    assert scan(shop, tmp_path / "root.json")["repository"] == "shop-api"
    sub = shop / "src" / "main" / "java"
    assert scan(sub, tmp_path / "sub.json")["repository"] == "java"
    assert scan(sub, tmp_path / "named.json", "--name", "orders")["repository"] == "orders"


def test_check_against_a_git_ref_in_one_command(shop, capsys):
    git(shop, "checkout", "-qb", "feature")
    write(shop, "src/main/java/shop/OrderResponse.java", RESPONSE.replace("    private String status;\n", ""))
    git(shop, "commit", "-qam", "drop status")
    capsys.readouterr()

    assert run("check", shop, "--base", "main") == cli.EXIT_FAILED
    assert "status" in capsys.readouterr().out
    assert run("check", shop, "--base", "feature") == 0          # same code on both sides: clear
    assert capsys.readouterr().out.startswith("✅ Clear")
    worktrees = subprocess.run(["git", "worktree", "list"], cwd=shop, capture_output=True, text=True).stdout
    assert len(worktrees.strip().splitlines()) == 1                  # temporary worktree cleaned up
    assert run("check", shop, "--base", "nope") == cli.EXIT_ERROR


def test_push_can_wait_until_the_server_documented_the_upload(shop, tmp_path, monkeypatch, capsys, caplog):
    import logging
    import time
    import requests

    states = []

    class Response:
        def __init__(self, data, status=202):
            self.data, self.status_code, self.ok, self.content = data, status, True, b"x"

        def json(self):
            return self.data

    monkeypatch.setattr(requests, "post", lambda url, json=None, headers=None, timeout=None:
                        Response({"status": "queued", "job_id": 7, "job_url": "/api/jobs/7"}))
    monkeypatch.setattr(requests, "get", lambda url, headers=None, timeout=None: Response(states.pop(0), 200))
    monkeypatch.setattr(time, "sleep", lambda s: None)

    states[:] = [{"id": 7, "status": "queued"}, {"id": 7, "status": "running", "progress_done": 2, "progress_total": 4},
                 {"id": 7, "status": "done", "result": {"created": 3, "unchanged": 1}}]
    caplog.set_level(logging.INFO, logger="mergeclear.cli")
    monkeypatch.setenv("MERGECLEAR_API_KEY", "k")
    assert run("scan", shop, "--push", "http://mc", "--wait") == 0
    assert "job 7: running, 2 of 4 endpoints" in caplog.text
    assert "documented: 3 new version(s), 1 unchanged" in caplog.text

    states[:] = [{"id": 7, "status": "failed", "error": "OperationalError: database is down\ntrace"}]
    report = tmp_path / "r.json"
    scan(shop, report)
    assert run("push", report, "--server", "http://mc", "--wait") == cli.EXIT_ERROR
    assert "could not document the upload: OperationalError: database is down" in capsys.readouterr().err

    assert run("scan", shop, "--wait") == cli.EXIT_ERROR            # nothing to wait for without --push


def test_server_answers_explain_what_to_do(shop, tmp_path, monkeypatch, capsys):
    import requests

    class Answer:
        ok, content, text = False, b"{}", "{}"

        def __init__(self, status):
            self.status_code = status

    report = tmp_path / "r.json"
    scan(shop, report)
    monkeypatch.setenv("MERGECLEAR_TOKEN", "old-name-still-works")
    for status, hint in ((401, "Settings → API keys"), (403, "must belong to an admin"), (503, "create the admin account")):
        seen = {}
        monkeypatch.setattr(requests, "post", lambda url, json=None, headers=None, timeout=None, s=status:
                            seen.update(headers=headers) or Answer(s))
        assert run("push", report, "--server", "http://mc") == cli.EXIT_ERROR
        assert hint in capsys.readouterr().err
        assert seen["headers"] == {"Authorization": "Bearer old-name-still-works"}


def test_uploading_needs_both_the_server_url_and_an_api_key(shop, monkeypatch, capsys):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("no request without both settings"))
    for name in ("MERGECLEAR_API_KEY", "MERGECLEAR_TOKEN", "MERGECLEAR_SERVER"):
        monkeypatch.delenv(name, raising=False)

    assert run("scan", shop, "--push") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "the server URL" in err and "an API key" in err           # both named in one message
    assert run("scan", shop, "--push", "http://mc") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "an API key" in err and "server URL" not in err
    monkeypatch.setenv("MERGECLEAR_API_KEY", "k")
    assert run("scan", shop, "--push") == cli.EXIT_ERROR
    assert "the server URL" in capsys.readouterr().err


def test_the_project_is_sent_when_named_else_left_to_the_api_key(shop, tmp_path, monkeypatch, caplog):
    import logging
    import requests

    sent = []

    class Answer:
        status_code, ok, content = 202, True, b"{}"

        def __init__(self, project):
            self.project = project

        def json(self):
            return {"status": "queued", "project": self.project}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return Answer(json.get("project") or "from-key")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("MERGECLEAR_API_KEY", "k")
    monkeypatch.setenv("MERGECLEAR_SERVER", "http://mc")
    caplog.set_level(logging.INFO, logger="mergeclear.cli")

    assert run("scan", shop, "--push") == 0
    assert sent[-1]["project"] is None                               # server uses the key's project
    assert sent[-1]["repository"] == "shop"                          # still filled, for older servers
    assert "as project from-key" in caplog.text

    assert run("scan", shop, "--push", "--project", "payments") == 0
    assert sent[-1]["project"] == sent[-1]["repository"] == "payments"
    monkeypatch.setenv("MERGECLEAR_PROJECT", "orders")
    assert run("scan", shop, "--push") == 0
    assert sent[-1]["project"] == "orders"
    monkeypatch.delenv("MERGECLEAR_PROJECT")
    (shop / "mergeclear.yml").write_text("project: billing\n")
    assert run("scan", shop, "--push") == 0
    assert sent[-1]["project"] == "billing"

    report = tmp_path / "r.json"
    report.write_text(json.dumps(sent[-1]))
    assert run("push", report, "--project", "renamed") == 0
    assert sent[-1]["project"] == "renamed"


def test_the_servers_reason_is_shown_when_it_refuses_an_upload(shop, tmp_path, monkeypatch, capsys):
    import requests

    class Answer:
        status_code, ok, content, text = 400, False, b"x", "x"

        def json(self):
            return {"error": "no project name: pass --project <name>"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: Answer())
    monkeypatch.setenv("MERGECLEAR_API_KEY", "k")
    assert run("scan", shop, "--push", "http://mc") == cli.EXIT_ERROR
    assert "refused the upload: no project name" in capsys.readouterr().err


def test_uploads_wait_and_retry_when_the_server_limits_them(shop, monkeypatch, capsys):
    import time
    import requests

    class Answer:
        content, text = b"x", "x"

        def __init__(self, status, headers=None, data=None):
            self.status_code, self.ok, self.headers, self.data = status, status < 400, headers or {}, data or {}

        def json(self):
            return self.data

    waits = []
    monkeypatch.setattr(time, "sleep", waits.append)
    monkeypatch.setenv("MERGECLEAR_API_KEY", "k")
    answers = [Answer(429, {"Retry-After": "7"}), Answer(429, {"Retry-After": "9999"}),
               Answer(202, data={"status": "queued", "project": "shop"})]
    monkeypatch.setattr(requests, "post", lambda *a, **k: answers.pop(0))
    assert run("scan", shop, "--push", "http://mc") == 0
    assert waits == [7, cli.MAX_RETRY_WAIT]                         # as asked, capped

    monkeypatch.setattr(requests, "post", lambda *a, **k: Answer(429, {"Retry-After": "1"}, {"error": "slow down"}))
    assert run("scan", shop, "--push", "http://mc") == cli.EXIT_ERROR
    assert "limiting uploads" in capsys.readouterr().err

    monkeypatch.setattr(requests, "post", lambda *a, **k: Answer(413, data={"error": "over the 25 MB limit"}))
    assert run("scan", shop, "--push", "http://mc") == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "too large" in err and "--commit" in err and "25 MB" in err
