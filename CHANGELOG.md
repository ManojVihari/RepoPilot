# Changelog

All notable changes. Versions follow [semantic versioning](https://semver.org);
a release is made by pushing the tag `vX.Y.Z` (see README "Releasing").

## [0.3.0] - unreleased

### Added
- Projects: `mergeclear scan --project NAME` (or `MERGECLEAR_PROJECT`, `project:` in
  `mergeclear.yml`). API keys can carry a default project, used when the pipeline
  names none; the key form explains this and shows the pipeline step to paste.
- Uploading requires both the server URL and an API key; the scanner names everything
  missing before it sends anything.
- Limits: request bodies over `MERGECLEAR_MAX_UPLOAD_MB` (25) get 413; uploads per
  account over `MERGECLEAR_UPLOADS_PER_MINUTE` (30) get 429 with `Retry-After`, which
  the scanner waits for and retries (up to 3 times).
- HTTPS: `docker-compose.https.yml` puts Caddy (automatic Let's Encrypt certificates,
  HSTS, HTTP→HTTPS) in front of the server; `MERGECLEAR_TRUSTED_PROXIES` for your own proxy.
- Backups: `scripts/backup.sh` (verified `pg_dump`, keeps the newest 14) and
  `scripts/restore.sh`; the smoke test does a backup → wipe → restore round trip.
- Releases: pushing a `vX.Y.Z` tag publishes `ghcr.io/<owner>/mergeclear-server` and
  `-scanner` (amd64 + arm64) and a GitHub release with the scanner's wheel and sdist.
  `scripts/build_dist.sh` builds and checks the PyPI files.

### Changed
- `docker compose up` pulls the released server image; `docker-compose.build.yml`
  builds from the checkout instead.
- The scanner is packaged with `pyproject.toml` (PyPI metadata, one version source).
- `--name` / `MERGECLEAR_NAME` / `name:` are now spelled `--project` /
  `MERGECLEAR_PROJECT` / `project:` (the old spellings still work).

### Fixed
- CI now runs on pushes to `master` (it only listened to `main`).

## [0.2.0]

First version under the Mergeclear name: Spring Boot and FastAPI scanner with
`scan`, `push`, `diff` and `check`; self-hosted server with versioned API docs,
history and diffs, architecture and dependency views, impact analysis, QA plans,
accounts with admin/viewer roles and API keys, Postgres with migrations and a job queue.
