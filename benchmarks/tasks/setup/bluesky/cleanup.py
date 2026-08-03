#!/usr/bin/env python3
"""Post-run cleanup of the REAL bsky.social benchmark account (reset_post hook).

Bluesky is the one surface the benchmark cannot reseed: the account lives on the
public bsky.social PDS, so instead of a pre-run seed we do a post-run sweep that
deletes everything the agent created during the run and restores the at-rest state:

  - posts / likes / reposts / follows created at-or-after --since (minus 60s clock
    skew) are deleted record-by-record via com.atproto.repo.* on the account's repo
  - ALL mutes are lifted (the bench account mutes nobody at rest; mute entries carry
    no timestamp so there is nothing to filter on)
  - bookmarks are deleted best-effort (they are private, so a leftover bookmark
    cannot pollute what other runs see; if the PDS lacks the endpoint we say so
    and move on)

Idempotent: re-running deletes nothing new and exits 0. Sessions are NOT touched -
we only mint a throwaway API session here; the iOS app's stored session in the
golden simulator is never invalidated (same contract as element/seed.py).

Usage (invoked by the harness as a reset_post hook):
    cleanup.py --since <unix-ts> [--creds <path>] [--dry-run]

Creds file: JSON {"handle": "...", "app_password": "..."} - lives OUTSIDE the repo
(see README.md / the benchmark host; e.g. a file on the operator's Desktop). Path
comes from --creds or $BSKY_CREDS_FILE. The password is never printed.

Exit nonzero on auth failure or any failed delete (the harness aborts the run).
Prints a one-line JSON summary to stdout.
"""
import argparse, datetime, json, os, re, sys, urllib.error, urllib.parse, urllib.request

PDS = "https://bsky.social"
SKEW = 60  # seconds of clock skew tolerated between harness and PDS timestamps
COLLECTIONS = ("app.bsky.feed.post", "app.bsky.feed.like",
               "app.bsky.feed.repost", "app.bsky.graph.follow")


def xrpc(nsid, token=None, params=None, body=None):
    """One XRPC call. GET when body is None, POST otherwise. Returns (status, json)."""
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


def parse_ts(iso):
    """createdAt (ISO8601, usually ...Z) -> unix seconds; unparsable -> None (kept, not deleted).
    Fractional seconds are normalized to 6 digits: pre-3.11 fromisoformat only accepts exactly 3
    or 6, and an unparsable bench record would silently SURVIVE cleanup (pollution)."""
    try:
        s = iso.replace("Z", "+00:00")
        s = re.sub(r"\.(\d{1,6})\d*", lambda m: "." + m.group(1).ljust(6, "0"), s)
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def load_creds(path):
    if not path:
        sys.exit("no creds: pass --creds or set BSKY_CREDS_FILE (see docs/surfaces/bluesky.md; "
                 "the file lives outside the repo on the benchmark host)")
    with open(os.path.expanduser(path)) as f:
        c = json.load(f)
    if not c.get("handle") or not c.get("app_password"):
        sys.exit(f"creds file {path} must be JSON with handle + app_password")
    return c


def main():
    ap = argparse.ArgumentParser(description="delete bench-account records created since a timestamp")
    ap.add_argument("--since", type=float, required=True, help="unix ts: run start (records >= since-60s go)")
    ap.add_argument("--creds", default=os.environ.get("BSKY_CREDS_FILE"), help="creds JSON path")
    ap.add_argument("--dry-run", action="store_true", help="list what would be deleted; delete nothing")
    args = ap.parse_args()
    cutoff = args.since - SKEW

    creds = load_creds(args.creds)
    st, ses = xrpc("com.atproto.server.createSession",
                   body={"identifier": creds["handle"], "password": creds["app_password"]})
    if st != 200 or "accessJwt" not in ses:
        sys.exit(f"auth failed for {creds['handle']}: HTTP {st} {ses.get('error', '')}")
    tok, did = ses["accessJwt"], ses["did"]

    failures = 0
    deleted = {}

    # 1. per-collection sweep: list every record, delete those created during the run
    for coll in COLLECTIONS:
        short = coll.rsplit(".", 1)[-1]  # post / like / repost / follow
        deleted[short] = 0
        cursor = None
        while True:
            params = {"repo": did, "collection": coll, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            st, res = xrpc("com.atproto.repo.listRecords", token=tok, params=params)
            if st != 200:
                print(f"listRecords {coll} failed: HTTP {st} {res.get('error', '')}", file=sys.stderr)
                failures += 1
                break
            for rec in res.get("records", []):
                ts = parse_ts(rec.get("value", {}).get("createdAt", ""))
                if ts is None or ts < cutoff:
                    continue  # pre-run record (or no timestamp): at-rest state, keep it
                rkey = rec["uri"].rsplit("/", 1)[-1]
                if args.dry_run:
                    print(f"would delete {short} {rec['uri']}")
                else:
                    dst, dres = xrpc("com.atproto.repo.deleteRecord", token=tok,
                                     body={"repo": did, "collection": coll, "rkey": rkey})
                    if dst != 200:
                        print(f"delete {rec['uri']} failed: HTTP {dst} {dres.get('error', '')}",
                              file=sys.stderr)
                        failures += 1
                        continue
                deleted[short] += 1
            cursor = res.get("cursor")
            if not cursor or not res.get("records"):
                break

    # 2. unmute everyone (no timestamps on mutes; at-rest the account mutes nobody)
    unmuted, cursor = 0, None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        st, res = xrpc("app.bsky.graph.getMutes", token=tok, params=params)
        if st != 200:
            print(f"getMutes failed: HTTP {st} {res.get('error', '')}", file=sys.stderr)
            failures += 1
            break
        for actor in res.get("mutes", []):
            if args.dry_run:
                print(f"would unmute {actor.get('handle', actor.get('did'))}")
            else:
                ust, ures = xrpc("app.bsky.graph.unmuteActor", token=tok, body={"actor": actor["did"]})
                if ust != 200:
                    print(f"unmute {actor.get('handle')} failed: HTTP {ust}", file=sys.stderr)
                    failures += 1
                    continue
            unmuted += 1
        cursor = res.get("cursor")
        if not cursor or not res.get("mutes"):
            break

    # 3. bookmarks: best-effort (private state - cannot pollute other runs). Older PDSes
    #    lack the app.bsky.bookmark.* lexicon and answer 400/404: report + continue.
    bookmarks = "ok"
    st, res = xrpc("app.bsky.bookmark.getBookmarks", token=tok, params={"limit": 100})
    if st in (400, 404):
        print("bookmarks: unsupported")
        bookmarks = "unsupported"
    elif st != 200:
        print(f"getBookmarks failed: HTTP {st} {res.get('error', '')}", file=sys.stderr)
        failures += 1
    else:
        for bm in res.get("bookmarks", []):
            uri = (bm.get("subject") or {}).get("uri") or bm.get("uri")
            if not uri:
                continue
            if args.dry_run:
                print(f"would delete bookmark {uri}")
                continue
            dst, _ = xrpc("app.bsky.bookmark.deleteBookmark", token=tok, body={"uri": uri})
            if dst in (400, 404):  # list worked but delete lexicon missing: same best-effort rule
                print("bookmarks: unsupported")
                bookmarks = "unsupported"
                break
            if dst != 200:
                print(f"delete bookmark {uri} failed: HTTP {dst}", file=sys.stderr)
                failures += 1

    print(json.dumps({"deleted": deleted, "unmuted": unmuted, "bookmarks": bookmarks}))
    if args.dry_run:
        sys.exit(0)  # a dry run never fails the harness on delete errors (there were none)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
