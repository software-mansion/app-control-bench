# Mastodon

**Tasks:** `mast-01` … `mast-30` (30) · **Bundle:** `com.example.IceCubesApp` · **Scripts:**
`tasks/setup/mastodon/` · **Status:** ✅ wired into `bench.APPS`

```python
"mastodon": {
    "bundle": "com.example.IceCubesApp", "open": None,
    "reset_pre": [PY, "tasks/setup/mastodon/seed.py"],
    "reset_timeout": 300,
}
```

## The app

Native **IceCubes**, a third-party Mastodon client, logged in (in the golden) to a **local Mastodon instance
on `:3000`**.

IceCubes is a stand-in: the official Mastodon iOS client would mean building `mastodon/mastodon-ios` from
source. IceCubes is a real, popular native client, which is good enough for measuring device control.

> **Port `:3000` collides with the Bluesky atproto PDS.** They cannot both be up. This is why the two
> surfaces have never run in the same session.

## Configuration

`seed.py` reads `$MASTODON_SEED_FILE` (default: `seed-config.json` next to the script, which is
**gitignored** because it holds access tokens):

```json
{
  "base_url": "http://localhost:3000",
  "bench_token": "...",
  "author_token": "...",
  "bench_display_name": "..."
}
```

Tokens come from Mastodon's Settings → Development → New application (scopes: `read`, `write`, `follow`), or
from `bin/tootctl`. See `seed-config.example.json`.

## The seed and its at-rest contract

`seed.py` runs as `reset_pre` before every run. It **never touches sessions or passwords**, so the IceCubes
login in the golden survives every reseed - the same invariant as [Element](element.md).

Two accounts:

- **BENCH** - the account IceCubes is logged in as. Wiped each run of statuses, boosts, favourites,
  bookmarks, mutes, blocks, stray follows, and display-name edits.
- **AUTHOR** - a fixture account that BENCH follows. **Its posts are the home timeline.**

The at-rest state the seed guarantees, and the tasks that depend on each:

| Guarantee | Tasks |
| --- | --- |
| Home timeline non-empty, with a plain "first post" | `mast-04/05/07/16/23/26` |
| A timeline post with an image attachment | `mast-25`, `mast-17` (Media tab) |
| A post containing "news", and one tagged `#art` | `mast-03`, `mast-14` (search) |
| A mention of bench → a notification | `mast-08`, `mast-20` (Mentions) |
| Author follows bench → non-empty followers list | `mast-22` |
| Bench display name = a canonical value | `mast-27` (appends `" (bench)"`) |
| Bench has 0 statuses / favourites / bookmarks | `mast-02/10/28/29/30` compose fresh; `mast-18/19` accept empty |
| Bench follows exactly `{author}` | `mast-24` re-follows someone |

If you add a mastodon task, add its data guarantee to `seed.py` **and** to this table - an unlisted
dependency is one reseed away from silently breaking.
