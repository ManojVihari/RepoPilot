#!/usr/bin/env bash
# Restore the Compose stack's Postgres database from a scripts/backup.sh dump.
# REPLACES everything in the database with the backup's contents.
#
#   scripts/restore.sh backups/mergeclear-20261009-021500.dump          # asks first
#   scripts/restore.sh --yes backups/mergeclear-20261009-021500.dump    # no question (automation)
#
# The server and workers are stopped during the restore and started again after.
# A backup from an older version is fine: the server migrates it on start.
set -euo pipefail
cd "$(dirname "$0")/.."

yes=""
[ "${1:-}" = "--yes" ] && { yes=1; shift; }
file="${1:?usage: scripts/restore.sh [--yes] BACKUP_FILE}"
[ -s "$file" ] || { echo "no such backup file: $file" >&2; exit 2; }

docker compose up -d --wait postgres >/dev/null
docker compose exec -T postgres pg_restore --list < "$file" > /dev/null \
  || { echo "$file is not a readable Mergeclear backup" >&2; exit 2; }

if [ -z "$yes" ]; then
  read -r -p "Replace ALL data in the Mergeclear database with $file? Type 'restore' to continue: " answer
  [ "$answer" = "restore" ] || { echo "cancelled"; exit 1; }
fi

running=$(docker compose ps --status running --services | grep -E '^(server|worker)$' || true)
echo "stopping: ${running:-nothing running}"
[ -n "$running" ] && docker compose stop server worker >/dev/null 2>&1 || true

docker compose exec -T postgres pg_restore --username mergeclear --dbname mergeclear \
  --clean --if-exists --no-owner --single-transaction --exit-on-error < "$file"
echo "restored $file"

if [ -n "$running" ]; then
  # shellcheck disable=SC2086
  docker compose start $running >/dev/null
  echo "started again: $running"
fi
