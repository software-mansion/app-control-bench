#!/usr/bin/env bash
# Start the deterministic Bluesky dev-env backend with throwaway local PostgreSQL + Redis.
# The process owns all three services; TERM/INT stops them. No Docker dependency.
set -euo pipefail

DEV_ENV="${BENCH_BSKY_DEV_ENV:-$HOME/Developer/bluesky/dev-env}"
STATE_DIR="$(mktemp -d /tmp/app-control-bench-bluesky.XXXXXX)"
readonly STATE_DIR
PG_DIR="$STATE_DIR/postgres"
PG_LOG="$STATE_DIR/postgres.log"
REDIS_PID="$STATE_DIR/redis.pid"
SERVER_PID=""
OWN_PG=0
OWN_REDIS=0
CLEANUP_STARTED=0

cleanup() {
  if [ "$CLEANUP_STARTED" = 1 ]; then return; fi
  CLEANUP_STARTED=1
  trap '' INT TERM

  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$OWN_REDIS" = 1 ] || [ -s "$REDIS_PID" ]; then
    redis-cli -p 6380 shutdown nosave >/dev/null 2>&1 || true
  fi
  if [ "$OWN_PG" = 1 ] || [ -s "$PG_DIR/postmaster.pid" ]; then
    pg_ctl -D "$PG_DIR" -m fast stop >/dev/null 2>&1 || true
  fi

  case "$STATE_DIR" in
    /tmp/app-control-bench-bluesky.??????) rm -rf -- "$STATE_DIR" ;;
    *) echo "refusing to remove unexpected state directory: $STATE_DIR" >&2 ;;
  esac
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for cmd in initdb pg_ctl pg_isready redis-server redis-cli; do
  command -v "$cmd" >/dev/null || { echo "missing required command: $cmd" >&2; exit 1; }
done
test -x "$DEV_ENV/node_modules/.bin/ts-node" || {
  echo "Bluesky dev-env is missing ts-node dependencies: $DEV_ENV" >&2
  exit 1
}
test -f "$DEV_ENV/bench-server.ts" || {
  echo "missing $DEV_ENV/bench-server.ts (copy from benchmarks/tasks/setup/bluesky/)" >&2
  exit 1
}

if ! pg_isready -h 127.0.0.1 -p 5433 >/dev/null 2>&1; then
  initdb -D "$PG_DIR" -U pg --auth=trust --no-locale >/dev/null
  pg_ctl -D "$PG_DIR" -l "$PG_LOG" -o "-p 5433 -h 127.0.0.1 -k $STATE_DIR" start >/dev/null
  OWN_PG=1
fi
if ! redis-cli -p 6380 ping >/dev/null 2>&1; then
  redis-server --bind 127.0.0.1 --port 6380 --save "" --appendonly no \
    --daemonize yes --pidfile "$REDIS_PID" --dir "$STATE_DIR"
  OWN_REDIS=1
fi

cd "$DEV_ENV"
NODE_ENV=development \
PGPORT=5433 PGHOST=127.0.0.1 PGUSER=pg PGPASSWORD=password PGDATABASE=postgres \
DB_POSTGRES_URL=postgresql://pg:password@127.0.0.1:5433/postgres \
REDIS_HOST=127.0.0.1:6380 \
  ./node_modules/.bin/ts-node ./bench-server.ts &
SERVER_PID=$!
wait "$SERVER_PID"
