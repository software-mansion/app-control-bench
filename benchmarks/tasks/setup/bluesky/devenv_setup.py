#!/usr/bin/env python3
"""One-time seed of the self-hosted atproto dev-env (PDS on localhost) for the Bluesky benchmark.

Builds a fully-controlled, deterministic Bluesky world, the atproto equivalent of the seeded
self-hosted Synapse we use for Element:

  - creates the accounts (no invite needed; PDS availableUserDomains = .test):
      bench.test      -> the account the benchmark agent logs into (the golden)
      whiskers.test, mittens.test  -> cat accounts (image posts)
      rex.test, buddy.test         -> dog accounts (image posts)
      newfriend.test               -> an account bench does NOT follow (target for the follow task)
      loudspammer.test             -> an account bench does NOT mute  (target for the mute task)
  - each content account posts its animal images with fixed captions + ordered createdAt
  - bench.test follows the 4 cat/dog accounts, so bench's Following feed is a deterministic,
    reproducible stream of cat + dog posts (first post = whiskers' newest, always)

Idempotent-ish: if an account already exists we log in and (optionally, with --wipe) delete its
existing posts before reposting, so re-running yields the identical state. Account DIDs are stable
for the life of the running dev-env network (do NOT recreate the network per run — that rotates DIDs
and breaks the golden's login; per-run reset instead wipes only the AGENT's mutations via cleanup.py
pointed at this same PDS).

Usage:
    devenv_setup.py [--pds http://localhost:3000] [--password hunter2] [--wipe] [--json-out <path>]

Prints a JSON summary (handles + DIDs + the bench creds) to stdout; writes it to --json-out if given
(the harness reads bench.test's handle/password from there for the golden login + per-run reset).
"""
import argparse, datetime, json, os, sys, urllib.error, urllib.parse, urllib.request

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# account -> list of (image-file, caption, alt). Order matters: index 0 is newest (first in feed).
CONTENT = {
    "whiskers.test": [
        ("cat1.jpg", "Mochi napping in a sunbeam \U0001F431 #caturday", "A kitten asleep in sunlight."),
        ("cat2.jpg", "someone is very much awake at 5am \U0001F408", "A cat staring at the camera."),
    ],
    "mittens.test": [
        ("cat3.jpg", "windowsill supervisor reporting for duty \U0001F63A", "A tabby cat on a windowsill."),
    ],
    "rex.test": [
        ("dog2.jpg", "beach day! best day \U0001F436 #dogsofbluesky", "A happy dog on a beach."),
        ("dog1.jpg", "he found the one muddy puddle in the whole park", "A muddy dog."),
    ],
    "buddy.test": [
        ("dog3.jpg", "good boy waiting patiently for a treat \U0001F415", "A dog sitting and waiting."),
    ],
}
BENCH = "bench.test"
FOLLOWS = ["whiskers.test", "rex.test", "mittens.test", "buddy.test"]   # bench follows these -> Following feed
EXTRA_ACCOUNTS = ["newfriend.test", "loudspammer.test"]                 # exist but bench does NOT follow


def req(url, method="GET", token=None, body=None, raw=None, ctype="application/json"):
    if isinstance(body, dict):
        data = json.dumps(body).encode()
    else:
        data = raw
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", ctype)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def ensure_account(pds, handle, pw):
    """Create the account if missing, else log in. Returns (did, token)."""
    st, res = req(f"{pds}/xrpc/com.atproto.server.createAccount", "POST",
                  body={"handle": handle, "password": pw, "email": handle.replace(".", "-") + "@bench.test"})
    if st == 200 and "accessJwt" in res:
        return res["did"], res["accessJwt"]
    # already exists (or other) -> log in
    st, res = req(f"{pds}/xrpc/com.atproto.server.createSession", "POST",
                  body={"identifier": handle, "password": pw})
    if st != 200 or "accessJwt" not in res:
        print(f"account {handle}: create+login failed HTTP {st} {res.get('error','')} {res.get('message','')}",
              file=sys.stderr)
        sys.exit(1)
    return res["did"], res["accessJwt"]


def wipe_posts(pds, did, token):
    for coll in ("app.bsky.feed.post",):
        st, res = req(f"{pds}/xrpc/com.atproto.repo.listRecords?"
                      + urllib.parse.urlencode({"repo": did, "collection": coll, "limit": 100}), token=token)
        for rec in res.get("records", []):
            rkey = rec["uri"].rsplit("/", 1)[-1]
            req(f"{pds}/xrpc/com.atproto.repo.deleteRecord", "POST", token=token,
                body={"repo": did, "collection": coll, "rkey": rkey})


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def create_post(pds, did, token, img, text, alt, when):
    with open(os.path.join(ASSETS, img), "rb") as f:
        raw = f.read()
    st, blob = req(f"{pds}/xrpc/com.atproto.repo.uploadBlob", "POST", token=token, raw=raw, ctype="image/jpeg")
    if st != 200 or "blob" not in blob:
        print(f"uploadBlob {img} failed HTTP {st} {blob.get('error','')}", file=sys.stderr); sys.exit(1)
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": iso(when), "langs": ["en"],
              "embed": {"$type": "app.bsky.embed.images", "images": [{"alt": alt, "image": blob["blob"]}]}}
    st, res = req(f"{pds}/xrpc/com.atproto.repo.createRecord", "POST", token=token,
                  body={"repo": did, "collection": "app.bsky.feed.post", "record": record})
    if st != 200:
        print(f"createRecord failed HTTP {st} {res.get('error','')}", file=sys.stderr); sys.exit(1)
    return res["uri"]


def follow(pds, did, token, subject_did):
    req(f"{pds}/xrpc/com.atproto.repo.createRecord", "POST", token=token,
        body={"repo": did, "collection": "app.bsky.graph.follow",
              "record": {"$type": "app.bsky.graph.follow", "subject": subject_did,
                         "createdAt": iso(int(datetime.datetime.now(datetime.timezone.utc).timestamp()))}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pds", default=os.environ.get("BENCH_PDS", "http://localhost:3000"))
    ap.add_argument("--password", default="hunter2")
    ap.add_argument("--wipe", action="store_true", help="delete existing posts before reposting")
    ap.add_argument("--json-out")
    a = ap.parse_args()
    pds, pw = a.pds.rstrip("/"), a.password

    accts = {}   # handle -> {did, token}
    for h in [BENCH] + list(CONTENT) + EXTRA_ACCOUNTS:
        did, tok = ensure_account(pds, h, pw)
        accts[h] = {"did": did, "token": tok}

    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    # content accounts post their images (newest = index 0). Stagger createdAt across accounts so the
    # merged Following feed has a stable, interleaved order.
    slot = now
    for h, posts in CONTENT.items():
        if a.wipe:
            wipe_posts(pds, accts[h]["did"], accts[h]["token"])
        for i, (img, text, alt) in enumerate(posts):
            create_post(pds, accts[h]["did"], accts[h]["token"], img, text, alt, slot)
            slot -= 180   # each subsequent post 3 min older -> deterministic global order

    # bench follows the cat/dog accounts -> Following feed populated
    if a.wipe:
        # clear bench's existing follows so we don't double-follow
        st, res = req(f"{pds}/xrpc/com.atproto.repo.listRecords?"
                      + urllib.parse.urlencode({"repo": accts[BENCH]["did"],
                                                "collection": "app.bsky.graph.follow", "limit": 100}),
                      token=accts[BENCH]["token"])
        for rec in res.get("records", []):
            rkey = rec["uri"].rsplit("/", 1)[-1]
            req(f"{pds}/xrpc/com.atproto.repo.deleteRecord", "POST", token=accts[BENCH]["token"],
                body={"repo": accts[BENCH]["did"], "collection": "app.bsky.graph.follow", "rkey": rkey})
    for h in FOLLOWS:
        follow(pds, accts[BENCH]["did"], accts[BENCH]["token"], accts[h]["did"])

    summary = {"pds": pds, "bench": {"handle": BENCH, "password": pw, "did": accts[BENCH]["did"]},
               "accounts": {h: accts[h]["did"] for h in accts},
               "follows": FOLLOWS, "content_accounts": list(CONTENT)}
    out = json.dumps(summary, indent=2)
    if a.json_out:
        open(a.json_out, "w").write(out)
    print(out)


if __name__ == "__main__":
    main()
