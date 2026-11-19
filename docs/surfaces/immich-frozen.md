# Immich benchmark surface — FROZEN lockfile

Exact versions/digests of the frozen Immich surface on **radoniusz**.
Regenerate after any intentional bump: `docker images --digests` + `/api/server/about`.

**Frozen on:** 2026-07-03 · **Host:** radoniusz.local (Apple silicon, macOS 15) · **Server:** http://localhost:2283

## Server release
| field | value |
|-------|-------|
| Immich version | **v3.0.1** (stable, released 2026-07-02) |
| source ref / commit | `v3.0.1` / `f77c8a4699aec043ec4a1b404559a7a291d592dc` |
| build | `28622919952` |
| bundled node / ffmpeg / exiftool | v24.14.1 / 7.1.4-3 / 13.59 |

## Pinned container images (digests)
```
ghcr.io/immich-app/immich-server:v3.0.1
    @sha256:46dedfc5848f7313bd6b584ea9f2648057430307aad6de56de968f6710a72cae
ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0
    @sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23
docker.io/valkey/valkey:9
    @sha256:4963247afc4cd33c7d3b2d2816b9f7f8eeebab148d29056c2ca4d7cbc966f2d9
```
`IMMICH_VERSION=v3.0.1` in `.env` pins the server (and, when enabled, the ML) image;
postgres + valkey are digest-pinned directly in `docker-compose.yml`.

## Docker host (Colima VM)
```
profile default · Running · aarch64 · 4 CPU · 6 GiB RAM · 40 GiB disk · runtime docker
socket: unix://$HOME/.colima/default/docker.sock
```
The 6 GiB VM is sized for the **no-ML** stack. Enabling ML (`--with-ml`) wants ~10 GiB —
`colima stop && colima start --memory 10 --cpu 6` before starting the ML profile.

## Admin account
```
email    admin@immich.local
password admin1234
name     Bench Admin
```
Local, disposable, LAN-only server — these demo credentials are intentionally simple.

## Seeded library (verified 2026-07-03)
```
66 images + 4 videos = 70 assets
albums:    Summer Vacation = 26   ·   City Trips = 12
favorites: 5      geotagged (Places): 34 markers
memories:  5 on-this-day      video-filter results: 4
metadata + thumbnail jobs: drained
```
Reproduce exactly: `./reset.sh` (erases + rebuilds + reseeds). Seed data is deterministic
(`random.seed(1337)`); memory dates re-derive from the current date on each run.

## iOS app (client)
| field | value |
|-------|-------|
| bundle id | `app.futo.immich.debug` (debug/simulator build; release would be `app.alextran.immich`) |
| built from | Immich mobile (Flutter 3.44.1), `~/dev/immich` at `main@237734b` (pubspec `3.0.0+3053`) — one commit ahead of the `v3.0.1` server tag, same day, compatible |
| toolchain | **Xcode 26.2** (Swift 6.2.3) — the version Immich CI pins. 26.5/27.0 fail on point-free swift-syntax deps. |
| build cmd | `./build-ios-app.sh <sim-udid>` — translation codegen → `flutter build ios --config-only` → `xcodebuild … -destination` (NOT `flutter build ios --simulator`, which uses `-sdk` and breaks host macros). |
| build status | ✅ **builds** — `Immich-Debug.app` (~227 MB) `BUILD SUCCEEDED`; installs, launches, connects to `http://localhost:2283`, reaches login. |
| server URL in app | `http://localhost:2283` (sim shares the host loopback) |
