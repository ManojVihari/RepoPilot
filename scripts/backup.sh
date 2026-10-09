#!/usr/bin/env bash
# Back up the Compose stack's Postgres database to a compressed, restorable file.
#
#   scripts/backup.sh                     # -> backups/mergeclear-YYYYmmdd-HHMMSS.dump
#   scripts/backup.sh /mnt/backups        # another folder (e.g. a mounted volume or NFS share)
#   MERGECLEAR_BACKUP_KEEP=30 scripts/backup.sh   # keep the newest 30 dumps there (default 14; 0 keeps all)
#
# Run it from cron on the Docker host, e.g. every night at 02:15:
#   15 2 * * * cd /opt/mergeclear && scripts/backup.sh >> backups/backup.log 2>&1
# and copy the folder off the machine (object storage, another host): a backup on
# the same disk does not survive losing that disk. Restore with scripts/restore.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

dir="${1:-backups}"
keep="${MERGECLEAR_BACKUP_KEEP:-14}"
mkdir -p "$dir"
file="$dir/mergeclear-$(date -u +%Y%m%d-%H%M%S).dump"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT
umask 077                                   # the dump holds password hashes and API key hashes

docker compose exec -T postgres pg_dump --username mergeclear --dbname mergeclear --format custom --compress 6 > "$partial"
# a dump pg_restore cannot read is not a backup
docker compose exec -T postgres pg_restore --list < "$partial" > /dev/null
mv "$partial" "$file"
echo "backup: $file ($(du -h "$file" | cut -f1))"

if [ "$keep" -gt 0 ]; then
  # newest first; delete everything after the first $keep
  ls -1t "$dir"/mergeclear-*.dump 2>/dev/null | tail -n +"$((keep + 1))" | while read -r old; do
    rm -f "$old" && echo "removed old backup: $old"
  done
fi
