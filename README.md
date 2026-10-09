# Mergeclear

**Know what a change breaks before you merge it.**

Mergeclear reads your source code, maps every endpoint, API contract and
dependency, and gives each change a verdict:

| Verdict | Meaning | `mergeclear check` |
|---|---|---|
| ✅ **Clear** | Nothing existing clients could notice (or only additions) | exit 0 |
| 🟡 **Review** | Clients may notice: new error codes, removed optional input, new or dropped downstream systems | exit 0 (exit 1 with `--fail-on review`) |
| 🔴 **Hold** | Breaking: removed or retyped fields, new required input, stricter validation, changed security, removed endpoints | exit 1 |

It has two parts:

- **`mergeclear` CLI** (open source, `scanner/`): scans a repository with
  tree-sitter (no build, no running app, no annotations), compares scans and
  gates merges through exit codes. It works on a laptop, in any CI system, in
  a git hook or a cron job. It only talks to git and the file system.
- **Mergeclear server** (self-hosted, `server/`): receives scans and turns them
  into versioned API docs (written by your local Ollama model), change history
  and diffs, impact analysis across services, an architecture map and QA plans.

Language support: Java / Spring in depth (Spring MVC, Spring Cloud Gateway, Feign, RestTemplate/WebClient, JPA/JDBC, MyBatis, Redis,
Spring Cache, Kafka, RabbitMQ, SQS, Spring Security, Maven and Gradle
multi-module builds); Python FastAPI at a basic level.

---

## Quick start: the CLI

```bash
# not on PyPI yet: install from this repository
pipx install "git+https://github.com/ManojVihari/RepoPilot.git#subdirectory=scanner"
# or, from a checkout:  pipx install ./scanner

cd ~/code/my-service
mergeclear check --base origin/main        # compare this checkout with main
```

`check --base` scans the base ref in a temporary git worktree and the current
folder (including uncommitted edits) as the head, then prints the verdict:

```
🔴 Hold: Breaking changes: existing clients fail until they are updated.
   4f2a91c03b1e → 9be7d2a1c4f0: 1 endpoint(s) changed · 2 breaking · 0 minor · 1 additive

🔴 POST /orders  (OrderController.create, changed)
    breaking  lines.warehouse (String) is required
    breaking  status: String → OrderStatus
    additive  trackingUrl (String)
```

### Commands

| Command | What it does |
|---|---|
| `mergeclear scan [PATH]` | Scan a folder into a report (JSON). Every endpoint by default; `--commit SHA` or `--since REF` for only what changed. `--out FILE` writes it; `--push [URL]` sends it to a server. |
| `mergeclear push REPORT` | Send a saved report to a server (scan on one machine, upload from another). |
| `mergeclear diff BASE HEAD` | Compare two reports. |
| `mergeclear check [HEAD] --base REF` / `--against BASE` | Compare and fail on Hold (or Review with `--fail-on review`). `HEAD` is a report or a folder (default `.`). |

`diff` and `check` take `--format text|markdown|json` and `--out FILE`. The
Markdown output is ready to post as a pull/merge request comment in any system.

**Exit codes:** `0` ok / clear · `1` check failed · `2` usage or runtime error.

### Settings

Flags win, then environment variables, then a `mergeclear.yml` in the scanned
folder:

| Setting | Flag | Environment | `mergeclear.yml` |
|---|---|---|---|
| Server URL | `--push URL`, `--server URL` | `MERGECLEAR_SERVER` | `server:` |
| Upload token | | `MERGECLEAR_TOKEN` | (never put tokens in the repo) |
| Repository name | `--name` | `MERGECLEAR_NAME` | `name:` |
| Branch (detached CI checkouts) | `--branch` | `MERGECLEAR_BRANCH` | `branch:` |

The repository name defaults to the git remote's name (when scanning the
repository root) or the folder name.

---

## Use it in any pipeline

There is no plugin to install: every recipe is the same two commands. Fetch
enough history for the base ref to exist.

**Any CI / shell**
```bash
pip install "git+https://github.com/ManojVihari/RepoPilot.git#subdirectory=scanner"
git fetch origin main
mergeclear check --base origin/main --format markdown --out mergeclear.md
```

**GitHub Actions**
```yaml
- uses: actions/checkout@v4
  with: { fetch-depth: 0 }
- run: pipx install "git+https://github.com/ManojVihari/RepoPilot.git#subdirectory=scanner"
- run: mergeclear check --base origin/${{ github.base_ref }} --format markdown --out mergeclear.md
```

**GitLab CI**
```yaml
mergeclear:
  image: python:3.12
  variables: { GIT_DEPTH: 0 }
  rules: [{ if: '$CI_PIPELINE_SOURCE == "merge_request_event"' }]
  script:
    - pip install "git+https://github.com/ManojVihari/RepoPilot.git#subdirectory=scanner"
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - mergeclear check --base origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME --format markdown --out mergeclear.md
  artifacts: { paths: [mergeclear.md], when: always }
```

**Jenkins**
```groovy
stage('Mergeclear') {
  steps {
    sh 'pip install --user "git+https://github.com/ManojVihari/RepoPilot.git#subdirectory=scanner"'
    sh 'git fetch origin main && ~/.local/bin/mergeclear check --base origin/main'
  }
}
```

**Git pre-push hook** (`.git/hooks/pre-push`)
```sh
#!/bin/sh
exec mergeclear check --base origin/main
```

**Keep the server's docs up to date** (after merges to main, or from cron)
```bash
MERGECLEAR_TOKEN=... mergeclear scan --branch main --push https://mergeclear.internal
```

---

## The server (self-hosted)

```bash
pip install ./scanner -r server/requirements.txt
cd server && python run.py              # http://localhost:8000
```

Then push scans to it (`mergeclear scan --push http://localhost:8000`), or
document a local folder in-process without a running server:

```bash
python scripts/document_local_repo.py ~/code/my-service            # add --no-llm to skip Ollama
```

| Environment variable | Default | Purpose |
|---|---|---|
| `MERGECLEAR_TOKEN` | unset (uploads open) | Bearer token scanners must send to `/analyze`. **Set it in any shared setup.** |
| `MERGECLEAR_DOCS_DIR` | `server/docs` | Generated documentation |
| `MERGECLEAR_DATABASE_DIR` | `server/database` | Versions, architecture models, QA plans |
| `MERGECLEAR_LLM` | `on` | `off` builds docs from scanned facts only |
| `OLLAMA_URL` | `http://localhost:11434/api/generate` | Ollama endpoint |
| `OLLAMA_MODEL` | `mistral` | Model for docs, titles, summaries and QA plans |

The old `DOCAI_*` variable names still work.

**Ollama:** install it from [ollama.com](https://ollama.com), then
`ollama pull mistral` and `ollama serve`. `python server/validate_ollama.py`
checks the setup. Without Ollama the server still works: docs come from the
scanned facts and QA plans from templates.

Health check: `GET /healthz`.

---

## Development

```bash
pip install -e ./scanner -r server/requirements.txt pytest
(cd scanner && python -m pytest)
(cd server && python -m pytest)
```

Licensed under the Apache License 2.0.
