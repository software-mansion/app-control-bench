#!/usr/bin/env python3
"""Point the Bluesky dev-client at the self-hosted stack instead of the real Bluesky.

Two entries in `~/dev/social-app/.env` are EMPTY out of the box, and each empty value silently
sends the app to production:

1. `EXPO_PUBLIC_BLUESKY_PROXY_DID=` -> the app falls back to `did:web:api.bsky.app`, so it sets
   `atproto-proxy: did:web:api.bsky.app#bsky_appview` on every app.bsky.* call and our local PDS
   dutifully proxies them to the REAL Bluesky AppView. The whole app then misbehaves in ways that
   look like unrelated bugs:
     - `app.bsky.actor.getPreferences` -> `{"error":"MethodNotImplemented"}` (it is a PDS method;
       the AppView does not implement it), so the app cannot read the account's birthdate and the
       **Age Assurance gate blocks the entire app**
     - the dev-env labeler DID -> `identity unknown` (it does not exist on real Bluesky)
     - the Following feed spins forever
   Setting it to the dev-env AppView DID fixes all three at once.

2. `GEOLOCATION_DEV_URL=` -> `GEOLOCATION_URL = IS_DEV ? (GEOLOCATION_DEV_URL ?? PROD) : PROD`.
   Nullish coalescing does NOT fall back on an empty string, so the URL becomes `""`, the app
   fetches `/geolocation`, that fails, and `countryCode` stays undefined ("getServerState: missing
   geolocation countryCode").

`EXPO_PUBLIC_CHAT_PROXY_DID` is deliberately LEFT empty: it makes the Chats tab fail with "could
not resolve iss did", which is the documented, expected end state for task bsky-13.

Values are inlined into the JS at bundle time, so **restart Metro with `-c` afterwards** or the old
bundle keeps the old (broken) values. Idempotent.
"""
import json
import os
import sys
import urllib.request

ENV = os.path.expanduser("~/dev/social-app/.env")
CONTROL_API = "http://localhost:1987/info"
GEOLOCATION_URL = "https://ip.bsky.app"


def appview_did():
    with urllib.request.urlopen(CONTROL_API, timeout=5) as r:
        return json.load(r)["appviewDid"]


def set_var(text, key, value):
    """Set key=value, whether it is currently empty or already set to something else."""
    out, found, changed = [], False, False
    for line in text.splitlines(keepends=True):
        if line.startswith(key + "="):
            found = True
            if line.strip() != "%s=%s" % (key, value):
                line = "%s=%s\n" % (key, value)
                changed = True
        out.append(line)
    if not found:
        sys.exit("%s not present in %s - refusing to invent it" % (key, ENV))
    return "".join(out), changed


src = open(ENV).read()
out, c1 = set_var(src, "EXPO_PUBLIC_BLUESKY_PROXY_DID", appview_did())
out, c2 = set_var(out, "GEOLOCATION_DEV_URL", GEOLOCATION_URL)

print("  EXPO_PUBLIC_BLUESKY_PROXY_DID : %s" % ("set" if c1 else "already correct"))
print("  GEOLOCATION_DEV_URL           : %s" % ("set" if c2 else "already correct"))

if not (c1 or c2):
    print("  nothing to do")
    sys.exit(0)

open(ENV + ".bak", "w").write(src)
open(ENV, "w").write(out)
print("  WROTE %s (backup at %s.bak)" % (ENV, ENV))
print("  now restart Metro with -c, or the old bundle keeps the old values")
