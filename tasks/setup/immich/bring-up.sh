#!/usr/bin/env bash
# Bring up the FROZEN Immich benchmark server (v3.0.1) on the radoniusz Mac.
# Idempotent: safe to re-run. Run from the immich-bench dir (where docker-compose.yml lives).
#
#   ./bring-up.sh            # default: server + db + redis, NO machine-learning (light)
#   ./bring-up.sh --with-ml  # also start immich-machine-learning (face rec / smart search)
#
# See README.md for the full runbook and the ML-toggle resource note.
set -eo pipefail

export PATH="/opt/homebrew/bin:$HOME/.node/bin:$PATH"
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

BASE="http://localhost:2283"
ADMIN_EMAIL="admin@immich.local"
ADMIN_PASSWORD="admin1234"
ADMIN_NAME="Bench Admin"

COMPOSE_ARGS=()
[ "${1:-}" = "--with-ml" ] && COMPOSE_ARGS=(--profile ml)

echo "==> Ensuring Colima (docker VM) is running"
if ! colima status >/dev/null 2>&1; then
  colima start
fi

echo "==> Ensuring .env exists"
[ -f .env ] || { cp env.example .env; echo "    created .env from env.example"; }

echo "==> docker compose up -d ${COMPOSE_ARGS[*]}"
docker compose "${COMPOSE_ARGS[@]}" up -d

echo "==> Waiting for $BASE/api/server/ping (up to ~5 min)"
ok=0
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/server/ping" || true)
  if [ "$code" = "200" ]; then ok=1; echo "    ping OK after ${i} tries"; break; fi
  sleep 5
done
[ "$ok" = "1" ] || { echo "!! server did not answer /ping"; docker compose logs --tail=40 immich-server; exit 2; }

echo "==> Server version:"
curl -s "$BASE/api/server/about" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("   ", d.get("version"))' || true

echo "==> Creating admin ($ADMIN_EMAIL) if not present"
resp=$(curl -s -X POST "$BASE/api/auth/admin-sign-up" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\",\"name\":\"$ADMIN_NAME\"}")
echo "    admin-sign-up -> $(echo "$resp" | head -c 200)"

echo "==> Stack status:"
docker compose ps

echo
echo "IMMICH BRING-UP DONE."
echo "  Server:  $BASE   (admin: $ADMIN_EMAIL / $ADMIN_PASSWORD)"
echo "  Next:    ./seed/seed.sh   to populate the benchmark library"
