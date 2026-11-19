#!/usr/bin/env bash
# Generate the deterministic library, then upload + organise it in Immich.
# Idempotent: re-run any time to refresh (memories re-derive from the current date).
set -eo pipefail
export PATH="/opt/homebrew/bin:$HOME/.node/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "==> generating assets ($PY)"
"$PY" "$HERE/generate_assets.py"
echo "==> uploading + organising"
"$PY" "$HERE/seed.py"
