#!/usr/bin/env bash
# End-to-end check of the released artefacts: builds both images, starts the
# compose stack (Postgres + server), creates an admin and an API key, uploads a
# scan with the scanner image and waits for it to be documented.
#
#   scripts/smoke_test.sh                      # from the repository root
#   SKIP_BUILD=1 scripts/smoke_test.sh         # reuse images built before
#   BUILD_ARGS="--build-arg PYTHON_IMAGE=..."  # restricted networks (see README)
#   KEEP=1 scripts/smoke_test.sh               # leave the stack running afterwards
set -euo pipefail
cd "$(dirname "$0")/.."

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-mergeclear-smoke}"
export MERGECLEAR_VERSION="${MERGECLEAR_VERSION:-smoke}"
export MERGECLEAR_PORT="${MERGECLEAR_PORT:-18000}"
export MERGECLEAR_DB_PASSWORD="${MERGECLEAR_DB_PASSWORD:-smoke$RANDOM$RANDOM}"
export MERGECLEAR_WORKERS=2
SERVER="http://localhost:${MERGECLEAR_PORT}"
SCANNER_IMAGE="mergeclear/scanner:${MERGECLEAR_VERSION}"
FIXTURE="$PWD/scanner/tests/fixtures/spring_shop"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*"; docker compose logs --tail 80 server || true; exit 1; }
cleanup() { [ "${KEEP:-}" = 1 ] || docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [ "${SKIP_BUILD:-}" != 1 ]; then
  step "build images"
  # shellcheck disable=SC2086
  docker build ${BUILD_ARGS:-} -f server/Dockerfile -t "mergeclear/server:${MERGECLEAR_VERSION}" .
  # shellcheck disable=SC2086
  docker build ${BUILD_ARGS:-} -t "$SCANNER_IMAGE" scanner/
fi

step "start Postgres + server"
docker compose up -d --no-build
for _ in $(seq 60); do curl -sf "$SERVER/healthz" >/dev/null && break; sleep 2; done
curl -sf "$SERVER/healthz" | grep -q '"database":"ok"' || fail "server not healthy"

step "a fresh server asks for setup and refuses uploads"
[ "$(curl -s -o /dev/null -w '%{redirect_url}' "$SERVER/ui")" = "$SERVER/setup" ] || fail "no redirect to /setup"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SERVER/analyze" -H 'content-type: application/json' -d '{}')" = 503 ] \
  || fail "upload before setup was not refused"

step "create an admin and an API key"
docker compose exec -T server python -m app.manage create-user --email smoke@example.com --role admin >/dev/null
KEY="$(docker compose exec -T server python -m app.manage create-api-key --email smoke@example.com --name smoke 2>/dev/null | tr -d '\r')"
case "$KEY" in mc_*) ;; *) fail "no API key created";; esac

step "uploads need a valid key"
code=$(docker run --rm --network host -v "$FIXTURE:/repo:ro" "$SCANNER_IMAGE" \
  scan --name smoke --push "$SERVER" >/dev/null 2>&1; echo $?)
[ "$code" = 2 ] || fail "upload without a key was not refused (exit $code)"

step "scan, upload and wait until documented"
docker run --rm --network host -e MERGECLEAR_API_KEY="$KEY" -v "$FIXTURE:/repo:ro" "$SCANNER_IMAGE" \
  scan --name smoke --push "$SERVER" --wait 300 || fail "upload or documentation failed"
jobs_json=$(curl -sf -H "Authorization: Bearer $KEY" "$SERVER/api/jobs?limit=1")
echo "$jobs_json" | grep -q '"status":"done"' || fail "job not done: $jobs_json"
echo "$jobs_json" | grep -q '"created":4' || fail "expected 4 documented endpoints: $jobs_json"

step "data survives a restart"
docker compose restart server >/dev/null
for _ in $(seq 60); do curl -sf "$SERVER/healthz" >/dev/null && break; sleep 2; done
apis=$(curl -sf -H "Authorization: Bearer $KEY" "$SERVER/api/jobs?status=done" | grep -o '"repo":"smoke"' | wc -l)
[ "$apis" -ge 1 ] || fail "data lost after restart"
revision=$(docker compose exec -T server python -m app.manage migrate 2>/dev/null | tr -d '\r')
echo "$revision"
case "$revision" in "schema at revision "*) ;; *) fail "migrations did not report a revision";; esac

printf '\n\033[32mSmoke test passed.\033[0m\n'
