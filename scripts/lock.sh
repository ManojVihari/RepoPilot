#!/usr/bin/env bash
# Regenerate the dependency lock files from their .in files (needs uv: pip install uv).
#
#   scripts/lock.sh            # after editing a .in file: add/remove packages, keep other pins
#   scripts/lock.sh --upgrade  # move every package to the newest version its range allows
#   scripts/lock.sh --check    # CI: fail if a lock is out of date with its .in file
set -euo pipefail
cd "$(dirname "$0")/.."

LOCKS=(scanner/requirements server/requirements requirements-dev)
mode="${1:-}"

compile() {   # $1 = base name, $2 = output file, extra args...
  local base="$1" out="$2"; shift 2
  uv pip compile "$base.in" --universal --python-version 3.10 --generate-hashes --quiet \
    --custom-compile-command "scripts/lock.sh" -o "$out" "$@"
}

if [ "$mode" = "--check" ]; then
  status=0
  for base in "${LOCKS[@]}"; do
    tmp="$(mktemp)"; cp "$base.txt" "$tmp"        # existing pins are kept as preferences
    compile "$base" "$tmp"
    if ! diff -q "$base.txt" "$tmp" >/dev/null; then
      echo "::error file=$base.txt::$base.txt is out of date with $base.in - run scripts/lock.sh"
      diff -u "$base.txt" "$tmp" | head -40
      status=1
    fi
    rm -f "$tmp"
  done
  [ $status = 0 ] && echo "lock files are up to date"
  exit $status
fi

for base in "${LOCKS[@]}"; do
  if [ "$mode" = "--upgrade" ]; then compile "$base" "$base.txt" --upgrade; else compile "$base" "$base.txt"; fi
  echo "locked $base.txt ($(grep -c '^[a-z]' "$base.txt") packages)"
done
