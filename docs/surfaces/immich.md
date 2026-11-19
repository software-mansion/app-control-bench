# Immich

**Tasks:** `immich-01` … `immich-30` (30) · **Bundle:** `app.futo.immich.debug` · **Scripts:**
`tasks/setup/immich/` · **Status:** ❌ **not wired into `bench.APPS`** (commented out)

This is the most completely built-out surface - **frozen, seeded, and verified** - and it is the template to
copy when adding a new one. It is a self-hosted photo server, not social media, so auth is a non-issue: we
own the server and the admin credentials.

## Topology

radoniusz → Colima VM → Docker:

| Container | Image | Port |
| --- | --- | --- |
| `immich_server` | `ghcr.io/immich-app/immich-server:v3.0.1` | **2283** |
| `immich_postgres` | `immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` (digest-pinned) | - |
| `immich_redis` | `valkey/valkey:9` (digest-pinned) | - |
| `immich_machine_learning` | behind the `ml` profile - **off by default** | - |

The simulator reaches the server on host loopback, so the in-app **Server URL is
`http://localhost:2283`** (or `http://<host>:2283` remotely).

**Admin login:** `admin@immich.local` / `admin1234`

## Quickstart

```bash
cd ~/immich-bench && ./bring-up.sh && ./seed/seed.sh && ./verify.sh
```

Redeploy from the repo:

```bash
rsync -a --exclude 'seed/.venv' --exclude 'seed/assets' --exclude 'library' --exclude 'postgres' \
  tasks/setup/immich/ radoniusz:immich-bench/
```

`reset.sh` is **destructive** and requires `--yes` / `-y` / `IMMICH_RESET_YES=1` non-interactively (its
`read` prompt aborts on a non-tty).

## The seeded library

Deterministic (`random.seed(1337)`), so the same dataset every time. `generate_assets.py` draws synthetic
JPEGs with Pillow and MP4s with ffmpeg, writes EXIF via exiftool (DateTimeOriginal, GPS, a scene name in
ImageDescription), and emits `assets.manifest.json`. `seed/seed.py` then logs in, uploads, creates albums,
sets favorites and memories, and kicks the metadata + thumbnail jobs.

| | |
| --- | --- |
| Assets | **66 images + 4 videos = 70**, dated 2019 → now |
| Albums | **Summer Vacation** (26), **City Trips** (12) |
| Favorites | 5 |
| Geotagged | 34 (New York / Paris / Tokyo / London / Barcelona / Miami / Sydney) → Places map |
| Memories | 5 "on this day" (2 photos each, last 5 years) |
| Scenes | `beach` / `mountain` / `city` / `people` / `nature` |

> **Memories re-derive from today's date.** If the run date has drifted, re-run `./seed/seed.sh` or the
> memories tasks will find nothing.

**Task coverage: 29/30 fully covered.** The exception is **`immich-15` (People)**, which needs `--with-ml`
*and* real face photos - drop portrait JPEGs into `seed/real_photos/`.

### Machine learning (off by default)

```bash
colima stop && colima start --memory 10 --cpu 6     # ML wants ~10 GiB
./bring-up.sh --with-ml
# then reprocess Smart Search + Face Detection from the admin UI
```

Without ML: **People (`immich-15`) is empty**, and **search (`immich-06`) falls back to metadata search** -
which still returns results, so the task remains solvable.

## Frozen versions

Frozen 2026-07-03 (see [`immich-frozen.md`](immich-frozen.md) for the full lockfile - image digests, Colima
spec, verified seed counts):

- **Immich v3.0.1**, commit `f77c8a4699aec043ec4a1b404559a7a291d592dc`, build `28622919952`
  (node v24.14.1, ffmpeg 7.1.4-3, exiftool 13.59).
- `IMMICH_VERSION=v3.0.1` - explicitly **not** a rolling `v3` / `release` tag.
- Colima: aarch64, 4 CPU / 6 GiB / 40 GiB disk.
- iOS app built from `~/dev/immich` at `main@237734b` (pubspec `3.0.0+3053`), Flutter 3.44.1, Xcode 26.2,
  `Immich-Debug.app` ≈ 227 MB.

## Building the iOS app: three hard-won fixes

`~/immich-bench/build-ios-app.sh "$UDID"`. It has to be built from source, and all three of these are
required:

1. **Use Xcode 26.2** (Swift 6.2.3). Xcode **26.5 fails** the swift-syntax macro plugin
   (`StructuredQueriesMacros produced malformed response`); **27.0 fails** a `swift-sharing` compile. This
   was the long-standing blocker that kept Immich out of the matrix.
2. **Run translation codegen first** - `dart run easy_localization:generate -S ../i18n` and
   `dart run bin/generate_keys.dart` - or `context.t` is undefined.
3. **Use `xcodebuild -destination`, not `flutter build ios --simulator`.** The Flutter path forces host-only
   macro plugins onto the simulator SDK and dies with
   `Cannot load module 'SwiftBasicFormat' built with SDK 'macosx'`.

Bundle id is `app.futo.immich.debug` (a release build would be `app.alextran.immich`).

## Wiring it in

Uncomment in `runner/bench.py` `APPS`:

```python
"immich": {"bundle": "app.futo.immich.debug", "open": None},
```

The app must also be installed and logged in **in the golden** - see
[`golden-simulator.md`](golden-simulator.md).

## Troubleshooting

- **After a reboot or OS update, Colima does not auto-start.** Re-run `bring-up.sh`. Data persists in the
  `./library` and `./postgres` bind mounts (verified across the 2026-07-07 macOS update).
- **Port 2283 busy** → an old stack is still up.
- Health check: `GET /api/server/about`.
- **`grep` is shadowed on radoniusz** - use `/usr/bin/grep`.
