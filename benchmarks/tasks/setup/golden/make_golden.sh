#!/usr/bin/env bash
# make_golden.sh — build + maintain the "golden" benchmark simulator (see README.md).
#
# The golden is a fully-provisioned, logged-in iOS simulator that is NEVER run by
# the benchmark itself: every run clones it (byte-identical data container) and the
# clone is deleted afterwards. This script guides the operator through creating,
# validating, and versioning that golden on the bench Mac ("radoniusz").
#
#   ./make_golden.sh create <device-type> <runtime>   # fresh staging sim + checklist
#   ./make_golden.sh from-existing <udid-or-name>     # clone an already-logged-in sim
#   ./make_golden.sh finalize [vN]                    # smoke-test + rename staging -> bench-golden-vN
#   ./make_golden.sh refresh-check                    # is the current golden safely Shutdown?
#
# Safety contract:
#   - refuses to run while any bench-golden-* simulator is Booted (except the ones a
#     subcommand itself manages) — a Booted golden means a bench may be mid-clone.
#   - only ever touches simulators named bench-golden-staging / bench-golden-v* /
#     bench-golden-smoketest, plus the explicit source of `from-existing`.
#   - on a SHARED dev machine, simulator creation must go through the device
#     allocator instead — this script is for the dedicated bench host only.
set -euo pipefail

STAGING="bench-golden-staging"
SMOKE="bench-golden-smoketest"
GOLDEN_RE='^bench-golden-v([0-9]+)$'
# apps every golden must carry, provisioned + logged in (see README.md checklist)
BUNDLES="xyz.blueskyweb.app im.vector.app com.example.IceCubesApp"

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "==> $*"; }

# "name<TAB>udid<TAB>state" for every simulator, via simctl's JSON (names can repeat
# across runtimes, so always resolve through this rather than trusting a bare name)
all_devices() {
    xcrun simctl list devices -j | python3 -c '
import json, sys
for devs in json.load(sys.stdin)["devices"].values():
    for d in devs:
        print("%s\t%s\t%s" % (d["name"], d["udid"], d["state"]))'
}

udid_of() {  # $1 = exact device name -> udid (empty if absent; dies if ambiguous)
    local hits
    hits=$(all_devices | awk -F'\t' -v n="$1" '$1 == n {print $2}')
    [ "$(echo "$hits" | grep -c . || true)" -le 1 ] || die "device name '$1' is ambiguous — clean up duplicates first"
    echo "$hits"
}

state_of() {  # $1 = udid
    all_devices | awk -F'\t' -v u="$1" '$2 == u {print $3; exit}'
}

# refuse to proceed while a bench-golden-* sim is Booted, except names given as args
assert_no_booted_golden() {
    local line name state allowed
    while IFS=$'\t' read -r name _ state; do
        case "$name" in bench-golden-*) ;; *) continue ;; esac
        [ "$state" = "Booted" ] || continue
        for allowed in "$@"; do [ "$name" = "$allowed" ] && continue 2; done
        die "$name is Booted — a bench may be using the golden right now (or it is no \
longer trusted; see README.md). Shut it down / re-golden before running this."
    done < <(all_devices)
}

next_version() {  # highest existing bench-golden-vN + 1 (1 if none)
    all_devices | awk -F'\t' '$1 ~ /^bench-golden-v[0-9]+$/ {
        sub(/^bench-golden-v/, "", $1); if ($1+0 > m) m = $1+0 } END { print m+1 }'
}

print_checklist() {
    cat <<'EOF'

Staging simulator is booted. Provision it BY HAND now (one time, ~30 min):

  0. BEFORE launching Bluesky, silence the Expo dev-menu onboarding sheet — it covers
     the app, and it is AX-opaque, so argent's taps cannot dismiss it:

       xcrun simctl spawn <udid> defaults write xyz.blueskyweb.app \
         EXDevMenuIsOnboardingFinished -bool YES

     Write it through `simctl spawn defaults`, NOT by editing the plist: the sim's
     cfprefsd caches the domain and overwrites a file edit.

  1. Install + log in each enabled app, per its tasks/setup/<app>/README.md:
       - Bluesky  (xyz.blueskyweb.app)      — sign in with the bench account; tap
         "Open" once on the "Open in Bluesky?" deep-link dialog so it never reappears
       - Element  (im.vector.app)           — log in as @alice; on the "Verify this
         device" screen do the one-time "Reset everything" flow (README caveat)
       - IceCubes (com.example.IceCubesApp) — log in to the local Mastodon (:3000)
         as the bench account
  2. Dismiss every one-time iOS dialog (keyboard tips, notification prompts, ...)
     so no run ever hits a first-launch modal.
  3. Run ONE throwaway agent-device session against this simulator so the XCTest
     automation runner gets installed into the sim (clones inherit it).
  4. Leave every app on a neutral screen (home feed), then:

       ./make_golden.sh finalize

EOF
}

enable_accessibility() {  # a fresh sim has NO accessibility server
    # Without this the sim exposes an EMPTY accessibility tree: `argent run describe` returns zero
    # elements and every tap lands nowhere, which reads as "argent is broken" rather than "the sim is
    # unprovisioned".
    #
    # Chicken-and-egg: `simctl spawn defaults` only works on a BOOTED device, but the AX server only
    # reads the setting when it starts. So: boot, write, then bounce the device.
    local udid="$1"
    xcrun simctl spawn "$udid" defaults write com.apple.Accessibility ApplicationAccessibilityEnabled -int 1
    xcrun simctl spawn "$udid" defaults write com.apple.Accessibility AccessibilityEnabled -int 1
    xcrun simctl shutdown "$udid"
    xcrun simctl bootstatus "$udid" -b
}

cmd_create() {
    [ $# -eq 2 ] || die "usage: $0 create <device-type> <runtime>"
    assert_no_booted_golden
    # capture first: udid_of's own die must not be swallowed by a $(...) in a test position
    local staging_udid
    staging_udid=$(udid_of "$STAGING")
    [ -z "$staging_udid" ] || die "$STAGING already exists — finalize or delete it first"
    say "creating $STAGING ($1, $2)"
    local udid
    udid=$(xcrun simctl create "$STAGING" "$1" "$2")
    say "booting $udid"
    xcrun simctl bootstatus "$udid" -b
    say "enabling the accessibility server (boot -> write -> bounce)"
    enable_accessibility "$udid"
    say "verify before provisioning: 'argent run describe --udid $udid' must list elements, not zero"
    print_checklist
}

cmd_from_existing() {
    # fast path: the first golden on radoniusz clones the already-logged-in bench sim
    [ $# -eq 1 ] || die "usage: $0 from-existing <udid-or-name>"
    assert_no_booted_golden
    # capture first: udid_of's own die must not be swallowed by a $(...) in a test position
    local staging_udid
    staging_udid=$(udid_of "$STAGING")
    [ -z "$staging_udid" ] || die "$STAGING already exists — finalize or delete it first"
    local src="$1"
    # accept a name or a udid; never accept one of our own managed sims as source
    case "$src" in bench-golden-*) die "source cannot be a bench-golden-* simulator" ;; esac
    if [ -z "$(state_of "$src")" ]; then
        src=$(udid_of "$src"); [ -n "$src" ] || die "no simulator matches '$1'"
    fi
    if [ "$(state_of "$src")" = "Booted" ]; then
        echo "WARNING: source $src is Booted — shutting it down PAUSES any bench/agent using it." >&2
        xcrun simctl shutdown "$src"
    fi
    say "cloning $src -> $STAGING (login/session data comes along byte-for-byte)"
    xcrun simctl clone "$src" "$STAGING"
    say "done. Boot it only if it needs touch-ups; otherwise run:  $0 finalize"
}

cmd_finalize() {
    [ $# -le 1 ] || die "usage: $0 finalize [vN]"
    assert_no_booted_golden "$STAGING"
    local staging_udid smoke_udid n shot
    staging_udid=$(udid_of "$STAGING")
    [ -n "$staging_udid" ] || die "no $STAGING simulator — run create/from-existing first"
    say "shutting down $STAGING"
    xcrun simctl shutdown "$staging_udid" 2>/dev/null || true   # already Shutdown is fine

    # leftover smoke clone from an aborted finalize is ours by name contract: remove it
    smoke_udid=$(udid_of "$SMOKE")
    if [ -n "$smoke_udid" ]; then
        say "removing leftover $SMOKE"
        xcrun simctl shutdown "$smoke_udid" 2>/dev/null || true
        xcrun simctl delete "$smoke_udid"
    fi

    say "smoke test: clone -> boot -> app containers -> screenshot"
    smoke_udid=$(xcrun simctl clone "$staging_udid" "$SMOKE")
    # whatever happens below, never leave the smoke clone behind
    trap 'xcrun simctl shutdown "$smoke_udid" 2>/dev/null || true; xcrun simctl delete "$smoke_udid" 2>/dev/null || true' EXIT
    xcrun simctl bootstatus "$smoke_udid" -b
    local b
    for b in $BUNDLES; do
        xcrun simctl get_app_container "$smoke_udid" "$b" >/dev/null \
            || die "smoke clone is missing $b — the staging sim is not fully provisioned"
        say "  ok: $b"
    done
    shot="/tmp/bench-golden-smoke-$(date +%Y%m%d-%H%M%S).png"
    xcrun simctl io "$smoke_udid" screenshot "$shot"
    say "  screenshot: $shot (eyeball it: springboard/apps present, no setup dialogs)"
    xcrun simctl shutdown "$smoke_udid"
    xcrun simctl delete "$smoke_udid"
    trap - EXIT

    n="${1:-}"; n="${n#v}"
    [ -n "$n" ] || n=$(next_version)
    say "renaming $STAGING -> bench-golden-v$n"
    xcrun simctl rename "$staging_udid" "bench-golden-v$n"
    cat <<EOF

GOLDEN READY: bench-golden-v$n ($staging_udid), state Shutdown.

  -> Update configs/golden.json to point the runner at bench-golden-v$n
     (schema owned by the harness — edit that file, not this script).
  -> Keep it Shutdown. If you ever find it Booted, it is no longer trusted:
     re-golden (see README.md).
EOF
}

cmd_refresh_check() {
    local found=0 name udid state
    while IFS=$'\t' read -r name udid state; do
        case "$name" in bench-golden-*) ;; *) continue ;; esac
        found=1
        if [ "$state" = "Shutdown" ]; then
            echo "$name  $udid  $state  (ok)"
        else
            echo "$name  $udid  $state  (NOT SAFE — a golden must stay Shutdown; if it was booted outside a clone flow, re-golden)"
        fi
    done < <(all_devices)
    [ "$found" = 1 ] || echo "no bench-golden-* simulators exist yet — run create/from-existing"
}

case "${1:-}" in
    create)        shift; cmd_create "$@" ;;
    from-existing) shift; cmd_from_existing "$@" ;;
    finalize)      shift; cmd_finalize "$@" ;;
    refresh-check) shift; cmd_refresh_check "$@" ;;
    *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
