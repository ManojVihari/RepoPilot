#!/usr/bin/env bash
# Build the scanner's PyPI distributions (sdist + wheel) into dist/, check them,
# and install the wheel into a throwaway virtualenv to prove it runs.
#
#   scripts/build_dist.sh              # needs the dev tools: pip install --require-hashes -r requirements-dev.txt
#   twine upload dist/*                # then publish (PyPI account + API token; see README "Releasing")
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(python -c "import re,io; print(re.search(r'__version__ = \"([^\"]+)\"', io.open('scanner/mergeclear/__init__.py').read()).group(1))")
rm -rf dist scanner/build scanner/*.egg-info
python -m build --outdir dist scanner/
python -m twine check --strict dist/*

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
python -m venv "$tmp/venv"
"$tmp/venv/bin/pip" install --quiet "dist/mergeclear-${version}-py3-none-any.whl"
installed=$("$tmp/venv/bin/mergeclear" --version)
[ "$installed" = "mergeclear $version" ] || { echo "installed wheel reports '$installed', expected 'mergeclear $version'"; exit 1; }

echo
echo "built and checked mergeclear $version:"
ls -1 dist/
