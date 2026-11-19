#!/usr/bin/env python3
"""Turn on Bluesky's built-in age-assurance debug mocks for dev builds.

Bluesky 1.122.0 gates the whole app behind an Age Assurance screen ("Hi there! ... we need to know
your birthdate") until it can prove the account is of age. Against the self-hosted atproto stack it
never can, so the golden can never reach the Following feed:

  * The app proxies its age-assurance prefetch to the PUBLIC AppView, and
    `https://api.bsky.app/xrpc/app.bsky.actor.getPreferences` answers
    `{"error":"MethodNotImplemented"}` - it is a PDS method, not an AppView one. The app therefore
    cannot read the birthdate, no matter what is set on our PDS. (Setting `personalDetailsPref` and
    `declaredAgePref` on the local PDS does nothing: verified, the gate stays.)
  * `app.bsky.ageassurance.getState` needs a `countryCode`, and the app's is empty (see the
    GEOLOCATION_DEV_URL note in docs/surfaces/bluesky.md), so server state fails too.

The app ships its own escape hatch for this - `src/ageAssurance/debug.ts` mocks the region config
(`access: 'full'`), the birthdate, and the server state (`status: 'assured'`). It is just wired to
`IS_E2E` only, behind switches written as `(IS_DEV && false)`, plainly meant to be flipped. This
flips them for dev builds, which is what the bench runs.

Scope: age assurance only. Feeds, posts, moderation and every task surface stay untouched, and no
bench task involves age assurance. Without it there is no bluesky golden at all.

Idempotent. `~/dev/social-app` is not a git checkout, so this lives here instead. Re-run after any
change to that file, and restart Metro with `-c` afterwards (the value is inlined at bundle time).
"""
import os
import sys

PATH = os.path.expanduser("~/dev/social-app/src/ageAssurance/debug.ts")

EDITS = [
    # (before, after) - the app's own switches, flipped from e2e-only to dev-too
    ("export const enabled = (IS_DEV && false) || IS_E2E",
     "export const enabled = (IS_DEV && true) || IS_E2E"),
    ("const serverStateEnabled = false || IS_E2E",
     "const serverStateEnabled = IS_DEV || IS_E2E"),
]

src = open(PATH).read()
out = src
applied, already = 0, 0

for before, after in EDITS:
    if after in out:
        already += 1
    elif before in out:
        out = out.replace(before, after)
        applied += 1
    else:
        sys.exit("neither the original nor the patched form of this line is present - the file "
                 "changed upstream, refusing to guess:\n  %s" % before)

print("  switches flipped   : %d" % applied)
print("  already patched    : %d" % already)

if out == src:
    print("  nothing to do")
    sys.exit(0)

open(PATH + ".bak", "w").write(src)
open(PATH, "w").write(out)
print("  WROTE %s (backup at %s.bak)" % (PATH, PATH))
print("  now restart Metro with -c, or the old bundle keeps the gate")
