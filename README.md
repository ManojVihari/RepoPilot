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
| API key (Settings → API keys on the server) | | `MERGECLEAR_API_KEY` | (never put keys in the repo) |
| Repository name | `--name` | `MERGECLEAR_NAME` | `name:` |
| Branch (detached CI checkouts) | `--branch` | `MERGECLEAR_BRANCH` | `branch:` |

The repository name defaults to the git remote's name (when scanning the
repository root) or the folder name.

---

## Use it in any pipeline

There is no plugin to install: every recipe is the same two commands. Uploading needs an admin's API key in `MERGECLEAR_API_KEY`. Fetch
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
MERGECLEAR_API_KEY=... mergeclear scan --branch main --push https://mergeclear.internal --wait
```
(`--wait` blocks until the server has documented the upload and fails if it could not.)

**No Python on the build machine?** Use the container (the repository is
mounted at `/repo`, so pass `--name` or keep a git remote for the repository name):
```bash
docker build -t mergeclear/scanner scanner/
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/repo" mergeclear/scanner check --base origin/main
```

---

## The server (self-hosted)

### With Docker Compose

```bash
cp .env.example .env            # set MERGECLEAR_DB_PASSWORD (openssl rand -hex 24)
docker compose up -d            # Postgres + server on http://localhost:8000
docker compose --profile llm up -d                          # optional: + Ollama (MERGECLEAR_LLM=on in .env)
docker compose --profile workers up -d --scale worker=3     # optional: more job workers
```

What runs:

- **postgres** (`postgres:17-alpine`): all data, in the `postgres-data` volume.
  Not published on the host; only the server and workers reach it.
- **server**: web UI and API, plus `MERGECLEAR_WORKERS` (default 2) background
  workers. Runs as a non-root user, waits for Postgres to be healthy, has a
  healthcheck on `/healthz` (503 when the database is unreachable), and refuses
  to start without `MERGECLEAR_DB_PASSWORD`.
- **worker** (profile `workers`): extra job workers on the same image. With an
  LLM, documenting uploads is the slow part; scale this instead of the server.
- **ollama** (profile `llm`): downloads `OLLAMA_MODEL` once into `ollama-models`.

**Accounts and API keys.**

1. Open the server: the first visit asks for the **admin account** (only while
   no user exists). Or create it from the shell:
   `docker compose exec server python -m app.manage create-user --email you@corp --role admin`.
2. Add people under **Settings → Users**: each gets a role and a temporary
   password (shown once) to replace at first sign-in. Admins can change roles,
   reset passwords and deactivate accounts (this ends their sessions and keys).
   `MERGECLEAR_ALLOW_SIGNUP=true` lets people create their own viewer account.
3. Create an **API key** under **Settings → API keys** for each pipeline or
   script, and give it to the scanner as `MERGECLEAR_API_KEY`. A key acts with
   its owner's role and is shown once; only a hash is stored. Keys show when
   they were last used and can be revoked (admins see and revoke everyone's).

| | viewer | admin |
|---|---|---|
| Read docs, history, impact, architecture, QA plans; ask questions | ✓ | ✓ |
| Own API keys (read the API) | ✓ | ✓ |
| Upload scans (`mergeclear scan --push`) | | ✓ |
| Regenerate QA plans, save test templates | | ✓ |
| Manage users and all API keys | | ✓ |

Everything except the product page, sign-in and `/healthz` needs a sign-in or an
API key. Passwords are hashed with scrypt, sessions are server-side (HttpOnly,
SameSite=Lax cookie), every browser request that changes something carries a
CSRF token, and repeated failed sign-ins lock the account for 15 minutes.
Locked out? `python -m app.manage reset-password --email you@corp`.

Put the server behind your usual reverse proxy for TLS (and set
`MERGECLEAR_COOKIE_SECURE=true`). Back up the database with
`docker compose exec postgres pg_dump -U mergeclear mergeclear > mergeclear.sql`.

**How uploads are processed.** `/analyze` stores the report as a job and
answers `202` with a `job_url` at once. Workers claim jobs from Postgres
(`FOR UPDATE SKIP LOCKED`), report progress, and retry failures with backoff
(3 attempts). A worker that dies loses its lease and the job is picked up again.
Identical uploads that are still queued are merged, and version numbers are
assigned under a per-API lock, so concurrent pipelines never collide. The
dashboard shows uploads in progress and recent failures; `mergeclear scan --push
--wait` blocks until the upload is documented. Job status: `GET /api/jobs`,
`GET /api/jobs/{id}`.

Building inside a restricted network:

| Situation | Build option |
|---|---|
| Docker Hub unreachable or rate-limited | `--build-arg PYTHON_IMAGE=registry.internal/python:3.12-slim` |
| TLS-inspecting proxy | `--secret id=ca,src=corporate-ca.pem` (used for pip/apt during the build only, not kept in the image) |
| Base image that already includes git (scanner) | the apt step is skipped |

```bash
docker build -f server/Dockerfile -t mergeclear/server .            # from the repository root
```

### Without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate           # Python 3.10+
pip install --require-hashes -r scanner/requirements.txt -r server/requirements.txt
pip install --no-deps -e ./scanner                            # the mergeclear CLI
cd server && python run.py              # http://localhost:8000 (development server with reload)
```

Without `MERGECLEAR_DATABASE_URL` the server uses a SQLite file
(`server/database/mergeclear.db`), fine for one person. Point it at Postgres for
anything shared:

```bash
export MERGECLEAR_DATABASE_URL=postgresql+psycopg://mergeclear:secret@db.internal:5432/mergeclear
python -m app.manage init-db             # tables (the server also creates them on start)
python -m app.manage worker --count 2    # workers in their own process (set MERGECLEAR_WORKERS=0 on the server)
```

**Upgrading from a version before the database** (docs in `server/docs`, JSON
in `server/database`): `cd server && python -m app.manage import-files`
copies versions, titles, architecture models, QA plans and templates in. It can
be run again safely.

Then push scans to it (`mergeclear scan --push http://localhost:8000`), or
document a local folder in-process without a running server:

```bash
python scripts/document_local_repo.py ~/code/my-service            # add --no-llm to skip Ollama
```

| Environment variable | Default | Purpose |
|---|---|---|
| `MERGECLEAR_ALLOW_SIGNUP` | `false` | `true` lets people create their own viewer account |
| `MERGECLEAR_SESSION_DAYS` | `7` | How long a sign-in lasts |
| `MERGECLEAR_COOKIE_SECURE` | `auto` | `true` behind a TLS-terminating proxy (cookie only sent over https) |
| `MERGECLEAR_DATABASE_URL` | SQLite in `MERGECLEAR_DATABASE_DIR` | `postgresql+psycopg://user:password@host:5432/db` |
| `MERGECLEAR_WORKERS` | `1` (`2` in compose) | Background job workers in the server process (`0`: run them separately) |
| `MERGECLEAR_DATABASE_DIR` | `server/database` | Where the SQLite default lives |
| `MERGECLEAR_LLM` | `on` | `off` builds docs from scanned facts only |
| `OLLAMA_URL` | `http://localhost:11434/api/generate` | Ollama endpoint |
| `OLLAMA_MODEL` | `mistral` | Model for docs, titles, summaries and QA plans |

The old `DOCAI_*` variable names still work.

**Ollama:** install it from [ollama.com](https://ollama.com), then
`ollama pull mistral` and `ollama serve`. `python server/validate_ollama.py`
checks the setup. Without Ollama the server still works: docs come from the
scanned facts and QA plans from templates.

Health check: `GET /healthz`.

Security: documentation is built from repository code and model output, so
every page sanitizes rendered HTML (allowlist) and sends a strict
Content-Security-Policy with a per-request script nonce.

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt   # scanner + server + pytest, ruff, pip-audit
pip install --no-deps -e ./scanner

ruff check .
(cd scanner && python -m pytest)
(cd server && python -m pytest)                       # SQLite
(cd server && MERGECLEAR_TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest)   # the same suite on Postgres
scripts/smoke_test.sh                                 # Docker: images + Postgres + server + scanner end to end
```

**Dependencies** are pinned with hashes. Edit the ranges in `scanner/requirements.in`,
`server/requirements.in` or `requirements-dev.in`, then run `scripts/lock.sh`
(needs `pip install uv`) to regenerate the `.txt` lock files; `scripts/lock.sh --upgrade`
moves everything to the newest allowed versions. The images install exactly these locks.

**Database changes** go through migrations: change the tables in `server/app/db.py`, then

```bash
cd server && alembic revision --autogenerate -m "add users.timezone"   # review the generated file
```

The server applies pending migrations when it starts (once, under a lock, even with several
workers); `python -m app.manage migrate` does it by hand. A test fails when the models and
the migrations disagree. Databases created before migrations existed are adopted automatically.

**CI** (`.github/workflows/ci.yml`) runs on every pull request: lint and lock-file check,
both test suites on Python 3.10 and 3.12 against SQLite and Postgres, a dependency audit
(also weekly), and the Docker smoke test.

Licensed under the Apache License 2.0.
