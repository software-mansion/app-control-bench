#!/usr/bin/env bash
# One command that runs EVERY missing benchmark run on its own, using the API keys already in the
# opencode auth store. Portable: no per-machine constant editing — paths are auto-detected (override
# with env). Resumable + self-healing: it resets phantom-masked ledger entries first, so a purged
# result can never be silently skipped.
#
# FULLY ISOLATED: each run clones the golden simulator (configs/golden.json — build it once with
# tasks/setup/golden/make_golden.sh), and a host-wide lock allows exactly ONE stream at a time; a
# second invocation fails loudly. There is no UDID knob any more — the device is a per-run clone.
#
#   ./run_all.sh                          # everything pending, all effort variants, both tools, bluesky
#   ONLY=gpt,gpt_high ./run_all.sh         # restrict models
#   TOOLS=argent ./run_all.sh              # restrict tools
#   APPS=bluesky,element ./run_all.sh      # restrict apps
#   SKIP_JUDGE=1 ./run_all.sh              # run the matrix but don't score
#
# Env passthrough to the engine: ONLY, TOOLS, TASK, APPS, RUN_TIMEOUT.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="runner:${PYTHONPATH:-}"
# run_all.sh is the DEDICATED-bench-host entrypoint: full kill scope, so teardown reaps ANY
# leaked opencode/MCP/proxy process (even orphans that escaped every ownership signal). On a
# shared dev machine run bench.py directly instead — its default "owned" scope only kills
# processes this harness spawned and loudly reports the rest.
export BENCH_KILL_SCOPE="${BENCH_KILL_SCOPE:-all}"

# 1. Preflight: fail before burning API spend if the surface is broken (unless FORCE=1). Covers the
#    device lock, the golden sim (present + Shutdown), binaries and API keys.
MODELS_ARG=(); [ -n "${ONLY:-}" ] && MODELS_ARG=(--models "$ONLY")
APPS_ARG=();   [ -n "${APPS:-}" ] && APPS_ARG=(--apps "$APPS")
if ! python3 runner/doctor.py --preflight "${MODELS_ARG[@]}" "${APPS_ARG[@]}"; then
  if [ "${FORCE:-0}" != "1" ]; then
    echo ">> preflight failed; fix the surface or re-run with FORCE=1 to proceed anyway." >&2
    exit 1
  fi
  echo ">> preflight failed but FORCE=1 — proceeding."
fi

# 2. Self-heal: reset any phantom-masked ledger entries so nothing missing is silently skipped.
python3 runner/doctor.py --heal "${MODELS_ARG[@]}" "${APPS_ARG[@]}" || true

# 3. Run every pending unit (resumable; fresh clone + fresh proxies + verified teardown per run).
echo ">> running the matrix..."
python3 runner/run_matrix.py

# 4. Score the captured screenshots (unless skipped).
if [ "${SKIP_JUDGE:-0}" != "1" ]; then
  echo ">> judging..."
  python3 runner/judge.py || echo ">> judge step reported issues (non-fatal)"
fi

# 5. Final coverage report — must show 0 pending and 0 masked gaps for a clean matrix.
echo ">> final coverage:"
python3 runner/doctor.py "${MODELS_ARG[@]}" "${APPS_ARG[@]}" || true
echo ">> done."
