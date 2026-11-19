#!/usr/bin/env bash
# Health-check the Immich benchmark surface (read-only). Wrapper around verify.py.
# Run after bring-up + seed, once metadata/thumbnail jobs have settled.
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/verify.py" "$@"
