#!/usr/bin/env bash
# DESTRUCTIVE: erase all Immich data and rebuild the frozen benchmark surface
# from scratch (fresh DB + library + reseeded photos). Use to return to a known
# clean state, or to refresh date-sensitive memories for a new run date.
#
#   ./reset.sh            # rebuild without machine-learning (default)
#   ./reset.sh --with-ml  # rebuild and start the ML container too
#   ./reset.sh --yes      # skip the confirmation prompt (also -y or IMMICH_RESET_YES=1);
#                         # required when run non-interactively (harness reset hook):
#                         # with `set -eo pipefail`, `read` on a non-tty stdin exits
#                         # nonzero and would abort the script before doing anything.
set -eo pipefail
export PATH="/opt/homebrew/bin:$HOME/.node/bin:$PATH"
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# --yes/-y (or IMMICH_RESET_YES=1) skips the interactive gate; remaining args pass through
ASSUME_YES="${IMMICH_RESET_YES:-0}"
PASS_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --yes|-y) ASSUME_YES=1 ;;
    *) PASS_ARGS+=("$arg") ;;
  esac
done
set -- ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}

if [ "$ASSUME_YES" != "1" ]; then
  read -r -p "This ERASES the Immich database + uploaded library. Continue? [y/N] " ok
  [ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "aborted"; exit 1; }
fi

echo "==> tearing down + removing volumes"
docker compose --profile ml down -v 2>/dev/null || docker compose down 2>/dev/null || true
echo "==> wiping bind-mount data (./library ./postgres)"
rm -rf ./library ./postgres

echo "==> rebuilding"
./bring-up.sh "$@"
./seed/seed.sh
echo "RESET COMPLETE."
