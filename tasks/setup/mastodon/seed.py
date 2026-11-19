#!/usr/bin/env python3
"""Idempotent reset+seed of the LOCAL Mastodon instance (:3000) for the 30 mast-* tasks.

Runs as a reset_pre hook: puts the server back into the exact fixture state every
mast-* task assumes, no matter what the previous agent run did. Two accounts:

  - BENCH account  - the one IceCubes is logged in as. We wipe everything it did
    (statuses, boosts, favourites, bookmarks, mutes, blocks, stray follows,
    display-name edits).
  - AUTHOR account - a fixture account the bench account follows; its posts ARE the
    bench home timeline. We create its posts once, matched by exact text.

At-rest state the tasks assume (see FIXTURES for the text -> task mapping):
  - home timeline non-empty with a plain "first post"   (mast-04/05/07/16/23/26)
  - a timeline post with an image attachment            (mast-25, mast-17 Media tab)
  - a post containing "news" + one tagged #art          (mast-03, mast-14 search)
  - a mention of the bench account -> a notification    (mast-08, mast-20 Mentions)
  - author follows bench -> non-empty followers list    (mast-22)
  - bench display name = canonical value                (mast-27 appends " (bench)")
  - bench has 0 statuses / favourites / bookmarks       (mast-02/10/28/29/30 compose
    fresh; mast-18/19 accept the empty state)
  - bench follows exactly {author}                      (mast-24 re-follows someone)

Idempotent: safe to run before every run. It NEVER touches sessions or passwords,
so the IceCubes login in the golden simulator stays valid (same contract as
element/seed.py's logout_devices=False).

Config: JSON file at $MASTODON_SEED_FILE (default: seed-config.json next to this
script; see seed-config.example.json - the real file holds access tokens and is
gitignored):
    {"base_url": "http://localhost:3000", "bench_token": "...",
     "author_token": "...", "bench_display_name": "..."}

Prints a one-line JSON summary; exits nonzero on any failure.
"""
import html.parser, io, json, os, re, struct, sys, time, urllib.error, urllib.parse, urllib.request
import uuid, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.environ.get("MASTODON_SEED_FILE", os.path.join(HERE, "seed-config.json"))

# ── fixture posts (AUTHOR account), matched by EXACT text after HTML-stripping ──
# (text, with_image). Posted in list order only when missing, so the LAST entry is
# the newest = "the first post in your home timeline" the interact tasks act on.
FIXTURES = [
    # mast-14: hashtag timeline for #art must have a post
    ("Enjoying some generative art this week. #art", False),
    # mast-03: a search for "news" must return something
    ("Daily news roundup: the benchmark server is up and running. #news", False),
    # mast-25 (image full-screen) + mast-17 (profile Media tab): a post WITH an image
    ("Fixture photo: a plain color test image for the benchmark.", True),
    # mast-04/05/07/16/23/26: the plain, newest post the agent favourites / boosts /
    # replies "agreed" to / bookmarks / opens the detail + actions menu of
    ("Welcome to the benchmark timeline. This is the fixture scenario post.", False),
]
# plus one dynamic fixture built in main() once we know the bench handle:
#   "@<bench> welcome to the benchmark instance!"  -> a Mentions notification
#   for mast-08 (Notifications non-empty) and mast-20 (Mentions filter)
MENTION_TEXT = "@{handle} welcome to the benchmark instance!"


# ── tiny HTTP layer (stdlib only) ────────────────────────────────────────────
def call(method, url, token, form=None, raw=None, ctype=None):
    """One API call. form = dict -> urlencoded body (Mastodon accepts it everywhere);
    raw/ctype for the multipart media upload. Returns (status, parsed-json, headers)."""
    data = None
    if form is not None:
        pairs = []
        for k, v in form.items():
            for item in (v if isinstance(v, list) else [v]):
                pairs.append((k, item))
        data = urllib.parse.urlencode(pairs).encode()
        ctype = "application/x-www-form-urlencoded"
    elif raw is not None:
        data = raw
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "null"), dict(e.headers)
        except Exception:
            return e.code, None, dict(e.headers)


def paginate(base, url, token):
    """GET url, following the Link: rel="next" header Mastodon uses for pagination."""
    out = []
    while url:
        st, body, hdrs = call("GET", url, token)
        if st != 200:
            raise RuntimeError(f"GET {url} -> HTTP {st}: {body}")
        out.extend(body or [])
        m = re.search(r'<([^>]+)>;\s*rel="next"', hdrs.get("Link", "") or "")
        url = m.group(1) if m else None
        if url and url.startswith("/"):  # be tolerant of relative Link targets
            url = base + url
    return out


class _Stripper(html.parser.HTMLParser):
    """Collapse a status' HTML content back to its plain text for exact matching.
    Mastodon renders '#art' / '@bench' as nested <a><span> - tag-stripping restores
    the original text; <br> and </p> become the newlines the composer inserted."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, d):
        self.parts.append(d)
    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag == "p":
            self.parts.append("\n")


def strip_html(content):
    p = _Stripper()
    p.feed(content or "")
    return "".join(p.parts).strip()


def tiny_png():
    """A 32x32 solid-teal PNG built from stdlib (no Pillow on the bench host's PATH)."""
    def chunk(typ, payload):
        c = typ + payload
        return struct.pack(">I", len(payload)) + c + struct.pack(">I", zlib.crc32(c))
    row = b"\x00" + b"\x2a\xa1\x98" * 32          # filter 0 + 32 RGB pixels
    ihdr = struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(row * 32)) + chunk(b"IEND", b""))


def upload_media(base, token, png):
    """POST /api/v2/media (multipart) -> media id; poll GET /api/v1/media/:id if 202."""
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
               f"filename=\"bench-fixture.png\"\r\nContent-Type: image/png\r\n\r\n".encode())
    body.write(png)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    st, res, _ = call("POST", f"{base}/api/v2/media", token, raw=body.getvalue(),
                      ctype=f"multipart/form-data; boundary={boundary}")
    if st not in (200, 202) or not res:
        raise RuntimeError(f"media upload -> HTTP {st}: {res}")
    mid = res["id"]
    for _ in range(30):                            # 202 = still processing; a 32x32 PNG is fast
        if st == 200:
            return mid
        time.sleep(1)
        st, res, _ = call("GET", f"{base}/api/v1/media/{mid}", token)
    raise RuntimeError(f"media {mid} never finished processing")


def main():
    try:
        with open(CONF) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        sys.exit(f"config missing: {CONF} (copy seed-config.example.json and fill in tokens)")
    base = cfg["base_url"].rstrip("/")
    btok, atok = cfg["bench_token"], cfg["author_token"]
    display = cfg.get("bench_display_name", "Bench User")

    st, bench, _ = call("GET", f"{base}/api/v1/accounts/verify_credentials", btok)
    if st != 200:
        sys.exit(f"bench_token rejected: HTTP {st}")
    st, author, _ = call("GET", f"{base}/api/v1/accounts/verify_credentials", atok)
    if st != 200:
        sys.exit(f"author_token rejected: HTTP {st}")
    summary = {"deleted_statuses": 0, "unreblogged": 0, "unfavourited": 0,
               "unbookmarked": 0, "unmuted": 0, "unblocked": 0, "unfollowed": 0,
               "fixtures_posted": 0, "fixtures_present": 0}

    # (a) reset the bench display name (mast-27 leaves it ending in " (bench)")
    if bench.get("display_name") != display:
        st, _, _ = call("PATCH", f"{base}/api/v1/accounts/update_credentials", btok,
                        form={"display_name": display})
        if st != 200:
            raise RuntimeError(f"display-name reset -> HTTP {st}")
    summary["display_name"] = display

    # (b) delete every bench status; a boost shows up here as a reblog wrapper, which
    # must be undone via unreblog on the ORIGINAL status, not DELETE on the wrapper
    for s in paginate(base, f"{base}/api/v1/accounts/{bench['id']}/statuses?limit=40", btok):
        if s.get("reblog"):
            st, _, _ = call("POST", f"{base}/api/v1/statuses/{s['reblog']['id']}/unreblog", btok, form={})
            key = "unreblogged"
        else:
            st, _, _ = call("DELETE", f"{base}/api/v1/statuses/{s['id']}", btok)
            key = "deleted_statuses"
        if st != 200:
            raise RuntimeError(f"cleanup of status {s['id']} -> HTTP {st}")
        summary[key] += 1

    # (c) unfavourite + unbookmark everything the agent starred/saved
    for verb, key in (("favourites", "unfavourited"), ("bookmarks", "unbookmarked")):
        for s in paginate(base, f"{base}/api/v1/{verb}?limit=40", btok):
            action = "unfavourite" if verb == "favourites" else "unbookmark"
            st, _, _ = call("POST", f"{base}/api/v1/statuses/{s['id']}/{action}", btok, form={})
            if st != 200:
                raise RuntimeError(f"{action} {s['id']} -> HTTP {st}")
            summary[key] += 1

    # unmute + unblock every account (mast-26 opens the actions menu with Mute/Block on
    # the fixture post; a stray mute/block hides the author's posts from the home timeline
    # while the follow stays intact, silently breaking every later home-timeline task)
    for verb, action, key in (("mutes", "unmute", "unmuted"), ("blocks", "unblock", "unblocked")):
        for acct in paginate(base, f"{base}/api/v1/{verb}?limit=40", btok):
            st, _, _ = call("POST", f"{base}/api/v1/accounts/{acct['id']}/{action}", btok, form={})
            if st != 200:
                raise RuntimeError(f"{action} {acct['acct']} -> HTTP {st}")
            summary[key] += 1

    # follows: bench must follow exactly {author} (home timeline = author's fixtures;
    # mast-24 re-follows a profile). Author follows bench so Followers (mast-22) is
    # non-empty. Both endpoints are idempotent.
    for acct in paginate(base, f"{base}/api/v1/accounts/{bench['id']}/following?limit=40", btok):
        if acct["id"] == author["id"]:
            continue
        st, _, _ = call("POST", f"{base}/api/v1/accounts/{acct['id']}/unfollow", btok, form={})
        if st != 200:
            raise RuntimeError(f"unfollow {acct['acct']} -> HTTP {st}")
        summary["unfollowed"] += 1
    for token, target in ((btok, author["id"]), (atok, bench["id"])):
        st, _, _ = call("POST", f"{base}/api/v1/accounts/{target}/follow", token, form={})
        if st != 200:
            raise RuntimeError(f"follow {target} -> HTTP {st}")

    # (d) author fixture posts: create only what is missing, matched by exact text
    have = set()
    for s in paginate(base, f"{base}/api/v1/accounts/{author['id']}/statuses"
                            "?limit=40&exclude_reblogs=true", atok):
        have.add(strip_html(s.get("content", "")).strip())
    wanted = [(t, img) for t, img in FIXTURES]
    wanted.append((MENTION_TEXT.format(handle=bench["acct"]), False))
    for text, with_image in wanted:
        if text in have:
            summary["fixtures_present"] += 1
            continue
        form = {"status": text, "visibility": "public"}
        if with_image:
            form["media_ids[]"] = [upload_media(base, atok, tiny_png())]
        st, _, _ = call("POST", f"{base}/api/v1/statuses", atok, form=form)
        if st != 200:
            raise RuntimeError(f"fixture post {text!r} -> HTTP {st}")
        summary["fixtures_posted"] += 1
        time.sleep(0.3)  # keep timeline order deterministic on first seed

    print(json.dumps(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"SEED FAILED: {e}", file=sys.stderr)
        sys.exit(1)
