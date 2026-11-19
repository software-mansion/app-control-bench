#!/usr/bin/env python3
"""Pre-run SEED of the REAL bsky.social benchmark account (reset_pre hook).

Element-style determinism for Bluesky. Bluesky can't reseed the whole public PDS
(the algorithmic Discover feed, other users), but it CAN put the *bench account*
into a known at-rest state so the account's own timeline and the "Following" feed
are byte-reproducible every run:

  - delete every existing bench post (app.bsky.feed.post) and follow
    (app.bsky.graph.follow) on the account's repo, so we start from a clean slate
  - upload the bundled cat images (assets/cat*.jpg) as blobs and create exactly N
    posts with those images + fixed captions + explicit, ordered createdAt, so the
    account's Following feed is always: cat #1 (newest / first) → #2 → #3
  - the account follows nobody, so "Following" shows ONLY these seeded posts

Idempotent: re-running deletes the previous seed and recreates the identical set.
Sessions are NOT touched — we mint a throwaway API session; the iOS app's stored
session in the golden simulator is never invalidated (same contract as cleanup.py
and element/seed.py). The post-run cleanup.py (reset_post) deletes only what the
AGENT created during the run (--since t0); these seed posts predate t0 and survive,
then the next run's seed replaces them.

Usage (invoked by the harness as a reset_pre hook):
    seed.py [--creds <path>] [--n 3] [--dry-run]

Creds file: JSON {"handle": "...", "app_password": "..."} outside the repo; path
from --creds or $BSKY_CREDS_FILE. The password is never printed. Exit nonzero on
any failure (the harness treats a failed reset as a surface error and keeps the
unit pending). Prints a one-line JSON summary to stdout.
"""
import argparse, datetime, json, os, sys, urllib.error, urllib.parse, urllib.request

PDS = "https://bsky.social"
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
# Fixed, ordered seed posts. Index 0 is the NEWEST (first in the Following feed) — tasks that open
# "the first post" always land on cat #1. Keep captions + order stable: they are the reproducible
# surface the benchmark and its judge rely on.
POSTS = [
    {"img": "cat1.jpg", "text": "Mochi the kitten napping in a sunbeam \U0001F431 #catsofbluesky",
     "alt": "A small tabby kitten curled up asleep in a patch of sunlight."},
    {"img": "cat2.jpg", "text": "Whiskers demanding dinner an hour early, as usual \U0001F408",
     "alt": "A fluffy cat looking up expectantly next to an empty food bowl."},
    {"img": "cat3.jpg", "text": "Pixel the tabby says good morning to the timeline \U0001F63A",
     "alt": "A tabby cat stretching on a windowsill in the morning light."},
]
COLLECTIONS_TO_WIPE = ("app.bsky.feed.post", "app.bsky.graph.follow")


def xrpc(nsid, token=None, params=None, body=None):
    """One JSON XRPC call. GET when body is None, POST otherwise. Returns (status, json)."""
    url = f"{PDS}/xrpc/{nsid}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def upload_blob(token, path):
    """POST com.atproto.repo.uploadBlob with raw JPEG bytes. Returns (status, blob-json)."""
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(f"{PDS}/xrpc/com.atproto.repo.uploadBlob", data=data, method="POST")
    req.add_header("Content-Type", "image/jpeg")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def load_creds(path):
    if not path or not os.path.exists(path):
        print(f"no creds: pass --creds or set BSKY_CREDS_FILE (got {path!r})", file=sys.stderr)
        sys.exit(2)
    d = json.load(open(path))
    h, p = d.get("handle"), d.get("app_password") or d.get("password")
    if not h or not p:
        print("creds file missing handle / app_password", file=sys.stderr)
        sys.exit(2)
    return h, p


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", default=os.environ.get("BSKY_CREDS_FILE"))
    ap.add_argument("--n", type=int, default=len(POSTS), help="how many seed posts (<= bundled)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    handle, pw = load_creds(a.creds)

    st, ses = xrpc("com.atproto.server.createSession",
                   body={"identifier": handle, "password": pw})
    if st != 200 or "accessJwt" not in ses:
        print(f"auth failed: HTTP {st} {ses.get('error', '')}", file=sys.stderr)
        sys.exit(1)
    tok, did = ses["accessJwt"], ses["did"]

    summary = {"deleted": {}, "created": 0, "dry_run": a.dry_run}

    # 1) wipe existing posts + follows so the seed is idempotent and Following == own posts
    for coll in COLLECTIONS_TO_WIPE:
        n = 0
        cursor = None
        while True:
            params = {"repo": did, "collection": coll, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            st, res = xrpc("com.atproto.repo.listRecords", token=tok, params=params)
            if st != 200:
                print(f"listRecords {coll} failed: HTTP {st} {res.get('error', '')}", file=sys.stderr)
                sys.exit(1)
            for rec in res.get("records", []):
                rkey = rec["uri"].rsplit("/", 1)[-1]
                if a.dry_run:
                    n += 1
                    continue
                dst, dres = xrpc("com.atproto.repo.deleteRecord", token=tok,
                                 body={"repo": did, "collection": coll, "rkey": rkey})
                if dst != 200:
                    print(f"deleteRecord {coll}/{rkey} failed: HTTP {dst} {dres.get('error', '')}",
                          file=sys.stderr)
                    sys.exit(1)
                n += 1
            cursor = res.get("cursor")
            if not cursor:
                break
        summary["deleted"][coll] = n

    # 2) create N seed posts. createdAt descends so index 0 is newest (first in the feed).
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    want = POSTS[: max(0, min(a.n, len(POSTS)))]
    for i, spec in enumerate(want):
        img_path = os.path.join(ASSETS, spec["img"])
        if not os.path.exists(img_path):
            print(f"missing asset {img_path}", file=sys.stderr)
            sys.exit(1)
        if a.dry_run:
            summary["created"] += 1
            continue
        bst, blob = upload_blob(tok, img_path)
        if bst != 200 or "blob" not in blob:
            print(f"uploadBlob {spec['img']} failed: HTTP {bst} {blob.get('error', '')}", file=sys.stderr)
            sys.exit(1)
        record = {
            "$type": "app.bsky.feed.post",
            "text": spec["text"],
            "createdAt": iso(now - i * 300),   # newest first: index 0 = now, each older by 5 min
            "langs": ["en"],
            "embed": {"$type": "app.bsky.embed.images",
                      "images": [{"alt": spec["alt"], "image": blob["blob"]}]},
        }
        cst, cres = xrpc("com.atproto.repo.createRecord", token=tok,
                         body={"repo": did, "collection": "app.bsky.feed.post", "record": record})
        if cst != 200:
            print(f"createRecord post {i} failed: HTTP {cst} {cres.get('error', '')}", file=sys.stderr)
            sys.exit(1)
        summary["created"] += 1

    print(json.dumps(summary))


if __name__ == "__main__":
    main()
