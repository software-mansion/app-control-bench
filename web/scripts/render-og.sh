#!/usr/bin/env bash
# Rasterise scripts/og-card.html -> static/og.png (1200x630, the size every social scraper expects).
#
# Run by hand after editing og-card.html; the result is committed, so neither CI nor `npm run build`
# needs a browser. prerender.mts copies static/og.png into public/ on every build.
#
# Served over HTTP rather than opened as file:// — the card pulls theme.css from runner/
# and its fonts from src/styles/fonts/, and Chrome blocks those cross-directory file:// subresources.
set -euo pipefail

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || { echo "Chrome not found at: $CHROME (override with CHROME=...)" >&2; exit 1; }

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
out="$here/../static/og.png"
port="${PORT:-8731}"

python3 -m http.server "$port" --bind 127.0.0.1 --directory "$repo" >/dev/null 2>&1 &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT
sleep 1

mkdir -p "$(dirname "$out")"
"$CHROME" --headless --disable-gpu --hide-scrollbars \
  --screenshot="$out" --window-size=1200,630 \
  "http://127.0.0.1:$port/web/scripts/og-card.html" >/dev/null 2>&1

echo "og card -> $(cd "$(dirname "$out")" && pwd)/$(basename "$out")"
