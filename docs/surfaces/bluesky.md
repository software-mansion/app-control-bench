# Bluesky

**Tasks:** `bsky-01` … `bsky-30` (30) · **Bundle:** `xyz.blueskyweb.app` · **Scripts:**
`tasks/setup/bluesky/` · **Status:** ✅ wired into `bench.APPS`

```python
"bluesky": {
    "bundle": "xyz.blueskyweb.app", "open": None,
    "reset_pre":  ["curl","-sf","-m","60","-X","POST","http://localhost:1987/reset"],
    "reset_post": ["curl","-sf","-m","60","-X","POST","http://localhost:1987/reset"],
    "reset_timeout": 120,
}
```

## The app

Native Bluesky, built from `~/dev/social-app`, installed as an **Expo dev-client** - meaning it loads its JS
from Metro at runtime rather than bundling it.

**Pinned to Bluesky 1.122.0** @ `7f5dd40` (`bluesky-social/social-app`, "Release prep 1.122.0", 2026-05-27).
The local checkout has no `.git`, so the commit is the upstream release commit. `bench.py` warns if the
local `~/dev/social-app` version drifts.

### Metro must be running

```bash
cd ~/dev/social-app && node node_modules/.bin/expo start --dev-client --port 8081   # keep it alive
```

**Free `:8081` first** (`lsof -nP -iTCP:8081 -sTCP:LISTEN`). If another Expo project is squatting the port,
`expo start` *silently* skips starting the dev server and the app never loads Bluesky - you get the
dev-client launcher screen instead.

> **Restart Metro with `-c` after ANY change to `node_modules`.** Metro's transform cache is keyed on the
> module graph it saw at startup, and it is long-lived - the instance on the bench host had been up for six
> days. Change a native dependency (e.g. pinning `react-native-screens` to 4.24.0) and rebuild the app, and
> Metro will happily keep serving the JS of the version you removed. The app then runs *new native + old JS*
> and the symptom is brutal to diagnose: it boots, executes JS, logs feature flags, hits the network, raises
> **no error and no redbox** - and renders nothing but the blue splash forever, with an empty accessibility
> tree. Killing Metro and restarting it with `-c` fixed it instantly.

A plain `xcrun simctl launch <udid> xyz.blueskyweb.app` works too (the dev-client reconnects to its last
URL), and that is what `bench.py` does (`"open": None`).

**One-time prime on a fresh sim:** the first deep-link after an erase pops an iOS *"Open in Bluesky?"*
dialog. Tap **Open** once; iOS then remembers, and every later launch is hands-off. Do this while building
the golden.

**Verify before a matrix:** `python3 runner/bench.py --verify-golden` - the Bluesky screenshot must show the
real Bluesky UI, **not** the dev-client "localhost:8081" launcher.

A standalone Release build would remove the Metro dependency entirely, but it is not needed for hands-off
operation.

## The backend - self-hosted atproto

Bluesky runs against a **self-hosted atproto stack**, not `bsky.social`.

| Service | Port |
| --- | --- |
| PDS | `:3000` |
| AppView | `:3002` (the next free port after PLC), reached by the app on `:2584` |
| PLC | `:3001` |
| Bench control API | `:1987` |

**Images go through `:2584`.** `test-pds.ts` gives the AppView `publicUrl: http://localhost:2584`, and every
image and avatar URL it hands out uses that host, but the AppView itself listens on `:3002`.
`bench-server.ts` bridges `:2584` to the AppView's real port when it starts. Without the bridge every feed
image and avatar is a grey placeholder and the image lightbox spins forever, which makes `bsky-18`
unsolvable: that was the state of the surface from its rebuild on 2026-11-17 until 2026-11-20. A backend
started before this fix can be bridged without a restart (a restart rotates DIDs): run a TCP forward from
`:2584` to `:3002`, e.g. `node appview-forward.mjs 3002` from the bench host's `~/dev/`. Check with
`curl -o /dev/null -w '%{http_code}' localhost:2584/img/avatar/plain/<did>/<cid>`.

`tasks/setup/bluesky/bench-server.ts` is the persistent backend. It differs from atproto dev-env's stock
`mock-server.ts` in the way that matters: **mock-server recreates the whole TestNetwork on every POST, which
rotates every account's DID** - that would break the golden's stored login. `bench-server.ts` creates the
network **once**, seeds a cat/dog world, stays up, and exposes `GET /info` and `POST /reset`.

Run it from the checkout's `dev-env/`, as an ES module on the checkout's Node 24:
`NODE_ENV=development ./dev-infra/with-test-redis-and-db.sh node ./bench-server.ts`. The wrapper starts the
Postgres and Redis the AppView needs, through `docker compose`.

### Rebuilding the surface on the host from nothing (done 2026-11-17)

The checkout, its `node_modules`, the backend files and the Docker images had all been removed from radoniusz.
What brought it back, in order:

1. Clone `bluesky-social/social-app` at `7f5dd40` into `~/dev/social-app`, then delete its `.git`. With Node
   24 (`~/.nvm`) and pnpm 11.1.3 (the checkout's package manager, not yarn): `pnpm install --frozen-lockfile`
   in the root and again in `dev-env/`. `react-native-screens` is already pinned to 4.24.0 at this commit.
   None of the native build repairs below are needed to serve JS to the app already in the golden.
2. Copy `tasks/setup/bluesky/bench-server.ts` to `dev-env/` and `tasks/setup/bluesky/assets/` to
   `dev-env/bench-assets/`.
3. `colima start`, and make sure `docker compose` works: Homebrew's plugin needs
   `"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]` in `~/.docker/config.json`.
4. Start the backend (command above) and wait for `GET :1987/info`.
5. `cp .env.example .env`, then `fix_env.py` and `disable_age_assurance.py` (both need the backend up).
6. Free `:8081` and start Metro: `node node_modules/.bin/expo start --dev-client --port 8081 -c`.
7. Sign the golden back in, on a clone, as described under
   [golden-simulator.md](golden-simulator.md#known-blocker-the-bluesky-rotating-refresh-token-caveat). A new
   backend means new DIDs, so the old session is dead. The first launch against a new Metro shows the
   dev-client launcher: tap the `http://localhost:8081` entry once. The test password is in `GET :1987/info`.

The backend and Metro are plain background processes: a reboot stops them, and a restarted backend needs
step 7 again. **Mac Janitor** (`~/dev/Mac-Janitor`, a launchd agent on that host) deletes idle build output
and Docker images; `~/dev/social-app`, the bench tree, the tools prefix, `~/apps` and `~/.agent-device` are on
its keep list (`~/Library/Application Support/MacJanitor/config.json`). Keep them there.

**Seeded accounts:** `bench.test` (the account the app is logged in as), `whiskers.test` / `mittens.test`
(cats), `rex.test` / `buddy.test` (dogs), `newfriend.test` (the follow-task target), `loudspammer.test` (the
mute-task target). At rest `bench.test` follows all four seeded animals (the `FOLLOWS` seed in
`bench-server.ts`), so an unfollow task (`bsky-24`) has a live "Following" button to toggle.

> **Do not blind-restart the dev-env.** Restarting rotates account DIDs, which breaks the golden's login and
> forces a re-golden. If it dies, the reset hooks hard-fail and units stay pending - that self-heals, and no
> data is corrupted. Fix the server; don't nuke it.
>
> If a restart does happen anyway (2026-07-17: a service bring-up recreated the containers), the app and its
> prefs on the golden survive - only the session is dead. Re-login **on the golden itself** with
> `~/dev/bench-iso/bsky_login.py` on radoniusz (model-driven argent sign-in: `bench.test` / the
> `bench-server.ts` seed password, hosting provider Custom → `http://localhost:3000`), then prove it with a
> cold-clone `--verify-golden` before running anything. The script strips the generated config's `provider`
> block first: it routes API calls through the per-run effort proxies (every port in
> `isolation.PROXY_SPECS`), which are down outside a run, and opencode hangs silently against them.

**Email confirmation is pre-marked** at seed time (`confirmBenchEmail()` sets `emailConfirmedAt`), so the
app never shows a "verify your email" modal on login or on reply. That modal used to silently block
compose tasks.

### Reset

`POST http://localhost:1987/reset`, both before and after every run.

`tasks/setup/bluesky/` also contains `seed.py` and `cleanup.py`, which are the **legacy** hooks from when
the benchmark ran against a real `bsky.social` account that could not be reseeded, only swept:

- `seed.py` - deleted every bench post/follow, uploaded `assets/cat*.jpg`, created N posts with ordered
  `createdAt` so the Following feed was always deterministic.
- `cleanup.py --since <ts>` - deleted every post / like / repost / follow the run created, lifted all mutes,
  best-effort bookmark deletion. `reset_pre` used `bsky_at_rest_ts` from `configs/golden.json` so residue
  from a crashed run self-healed.

These are no longer in the run path (`:1987/reset` replaced them) but are kept because they are the only way
to clean the real account if it is ever used again. `cleanup.py` was verified against the real account
(deleted 15 posts / 9 likes / 6 reposts / 9 follows / 4 mutes; idempotent).

## Backend limits that shape the tasks

The self-hosted stack cannot render everything the hosted product can. These are **not bugs** - the task
`solved_screen` criteria were written to match what the app actually shows:

- **Chats/DMs** - no chat service. The Chats tab always shows *"Whoops! could not resolve iss did"* + Retry.
  **That error screen is the correct end state** for `bsky-13`.
- **Feeds** - no custom feeds, so the Feeds tab shows "No results found" (`bsky-15`); the only saved feed is
  "Following" (≈ home).
- **Hashtags** - no hashtag-feed page; a hashtag lands on Explore search results (Top tab). Seeded tag is
  `#caturday` (`bsky-16`).

## Known blockers

**The dev-client build is fragile on Xcode 26 + RN 0.81.** Rebuilding from `~/dev/social-app` has cascaded
through: a Hermes phase script with a machine-specific node path baked into `ios/.xcode.env.local`; raw
`xcodebuild` missing RN codegen output; `expo run:ios` failing on a Clang explicit-modules resolution error
for `react-native-keyboard-controller`; and `pod install` failing on BitdriftCapture `Podfile.lock` drift.
Budget real time for a rebuild. A *build* alone is safe to run beside a live matrix (it is CPU-only), but do
not boot or install onto a sim while one is running - that trips the foreign-sim guard and aborts the run.

## Building the app: four repairs, in order

> **2026-07-14: this works.** It produced `Bluesky.app` 1.122.0 (arm64, Debug, dev-client), which is kept at
> `~/apps/Bluesky.app` + `~/apps/Bluesky-1.122.0-debug-arm64.tar.gz` on the bench host.

Nothing about this checkout builds out of the box on Xcode 26. Four independent defects sit behind each
other, and each only surfaces once the previous is fixed - so the first error you see is never the whole
story, and a fix that "reveals a new error" is usually working. **Order matters** (`yarn` wipes
`node_modules`, `pod install` regenerates `Pods/`, so the edits into those go after their installer):

```bash
cd ~/dev/social-app
yarn add --exact react-native-screens@4.24.0                # 1 - RNScreens 4.25.2 breaks the old arch
python3 <repo>/tasks/setup/bluesky/reanimated_includes.py   # 2 - includes C++23 stops pulling in
cd ios && pod install                                       #     (regenerates Pods/ for 4.24.0)
python3 <repo>/tasks/setup/bluesky/pods_cxx23.py            # 3 - RNReanimated -> C++23  (AFTER pod install)
python3 <repo>/tasks/setup/bluesky/fix_pbxproj.py           # 4 - duplicate Xcode targets
xcodebuild -workspace Bluesky.xcworkspace -scheme Bluesky -configuration Debug \
  -sdk iphonesimulator -derivedDataPath /tmp/bsky-dd \
  -arch arm64 ONLY_ACTIVE_ARCH=YES CODE_SIGNING_ALLOWED=NO build
```

Every script is idempotent, so re-running the whole block is always safe. What survives what:

| edit | target | survives `pod install`? | survives `yarn install`? |
| --- | --- | --- | --- |
| `fix_pbxproj.py` | `Bluesky.xcodeproj` | yes (verified) | yes |
| `pods_cxx23.py` | `Pods/Pods.xcodeproj` | **no - re-run it** | yes |
| `reanimated_includes.py` | `node_modules/` | yes | **no - re-run it** |

`pod install` itself works (13 s, 161 pods) despite the BitdriftCapture warning in the older notes below.

**1. A repeated prebuild duplicates the generated targets.** `expo prebuild` does not replace what it
generated last time, it *appends*: you end up with `BlueskyClip` and a second, quoted-name `"BlueskyClip"`
target (plus `"BlueskyNSE"`, `"Share-with-Bluesky"`), each with fresh UUIDs. The app target then depends on
two targets building the same product, and the build dies **in the planning phase, before compiling
anything**:

```
error: Multiple commands produce '.../BlueskyClip.app'
error: Multiple commands produce conflicting outputs      (x2, the .appex copies)
```

Repair with **`tasks/setup/bluesky/fix_pbxproj.py`** (run it from `ios/`; it is idempotent, derives the
UUIDs rather than hardcoding them, and keeps whichever of a colliding pair the app actually embeds):

```bash
cd ~/dev/social-app/ios && python3 fix_pbxproj.py     # --dry-run to look first
xcodebuild -list -project Bluesky.xcodeproj           # each target should appear once
```

**2. `RNReanimated` needs C++23 on the Xcode 26 SDK.** The Pods build at `c++20`, but Xcode 26's libc++
refuses any translation unit that pulls in both `<atomic>` and `<stdatomic.h>` below C++23 - and
`REANodesManager.mm` does:

```
error: <atomic> is incompatible with <stdatomic.h> before C++23. Please compile with -std=c++23.
```

**Do not fix this by passing `CLANG_CXX_LANGUAGE_STANDARD=c++23` to `xcodebuild`.** A command-line setting
applies to *every* target in the workspace, and `React-hermes` does not compile under C++23 (it fails on
`sizeof` of an incomplete `HermesRuntimeAgentDelegate::Impl`). You trade one broken pod for another. The
bump has to be scoped to the single target that needs it - which is what
**`tasks/setup/bluesky/pods_cxx23.py`** does:

It edits `Pods/Pods.xcodeproj`, because CocoaPods writes the standard into the pods' **pbxproj build
settings**, which override the per-target `.xcconfig` - so appending to the xcconfig does nothing. (Note the
Pods project mints *14*-character UUIDs, not the 24-character ones Xcode uses - a pbxproj script written
against one silently matches nothing in the other.)

The atomic error is *not* arch-specific; it fails identically on x86_64 and arm64. It is easy to misread the
first failing log as "x86_64 only", because the x86_64 slice aborts the build before the arm64 compile of the
same file is reached. `-arch arm64` is not a fix, just a halving of build time - the bench host is Apple
Silicon, so its simulators only ever run the arm64 slice.

**3. C++23 then breaks Reanimated's own sources.** Under C++23 libc++ stops pulling `<algorithm>`,
`<iterator>`, `<atomic>` and `<mutex>` in transitively, so ~19 Reanimated files that used `std::any_of`,
`std::istream_iterator`, `std::atomic`, `std::mutex` without including them stop compiling
(`no member named 'any_of' in namespace 'std'`). **`tasks/setup/bluesky/reanimated_includes.py`** scans the
package and adds only the missing includes. Note it anchors on `#include` *or* `#import`: the `.mm` files use
`#import` exclusively, and a scanner that looks only for `#include` silently skips exactly the Objective-C++
files that need patching.

**4. `react-native-screens` 4.25.2 does not compile on the old architecture - pin 4.24.0.** Bluesky runs the
**old** architecture deliberately (`app.config.js: newArchEnabled: false`, `ios/Podfile.properties.json:
"newArchEnabled": "false"`), so `RCT_NEW_ARCH_ENABLED` is defined nowhere. But `package.json` allows
`^4.24.0`, and `yarn.lock` resolves that to **4.25.2**, whose `ios/tabs/**` sources reference the ivar,
`rnscreens::conversion::` and `RCTStringFromNSString` *outside* their `#if RCT_NEW_ARCH_ENABLED` guards:

```
error: use of undeclared identifier '_reactEventEmitter'
error: use of undeclared identifier 'rnscreens'
```

16 of the 19 files under `ios/tabs/` are affected, so patching them is a fork, not a repair. **And you cannot
just exclude the directory**: `RNSScreenStack.mm` - core old-arch code - has 7 *unguarded* references to
`RNSTabs*` classes, so the old arch genuinely links against them. 4.25.2 simply regressed old-arch support.

`yarn add --exact react-native-screens@4.24.0` is the fix. Verified it moves nothing else: RN 0.81.5,
Reanimated 3.19.5, Expo 54.0.35 and gesture-handler 2.28.0 are all byte-identical afterwards.

> Note `yarn` re-materialises `node_modules`, so it **reverts repair 2** (and prints 3 pre-existing
> `patch-package` failures - `react-native-compressor`, `react-native-keyboard-controller`, `sonner-native` -
> whose patch files have drifted from their locked versions. Unrelated to this build; they predate it.)

`npx expo run:ios --device <name-or-udid>` remains broken on this Xcode: it resolves a *simulator* as a
physical device (`Unexpected devicectl JSON version output`) and dies on `No code signing certificates are
available`. Use `xcodebuild` + `simctl install`.

### Two things that will bite you after the build

**Sign the build, even for the simulator.** With `CODE_SIGNING_ALLOWED=NO` the app gets no entitlements, so
keychain access fails and every launch raises a dev **LogBox toast** over the bottom of the screen:

```
[expo-notifications] Error reading persisted server registration info:
  Keychain access failed: A required entitlement isn't present.
```

That toast sits exactly where the **tab bar** is, so it would cover a tap target and appear in every run's
screenshots. Simulator builds ad-hoc sign without a certificate - just pass
`CODE_SIGN_IDENTITY="-" CODE_SIGNING_REQUIRED=NO CODE_SIGNING_ALLOWED=YES` instead.

**The appexes come out empty and block `simctl install`:**

```
Bluesky.app/PlugIns/BlueskyNSE.appex is missing its bundle executable
```

The app embeds the appex built by the *phantom* NSE target (see repair 1), whose Sources phase is empty, so
it produces a bundle with no binary. There is no collision to catch there - only the phantom is wired in as a
dependency, and the real `BlueskyNSE` / `Share-with-Bluesky` targets are orphans. The bench needs neither
extension (`BlueskyNSE` is a push-notification service; `Share-with-Bluesky` is invoked from *other* apps'
share sheets), so the app is shipped with `PlugIns/` deleted. Rewiring the app onto the real extension
targets is the proper fix, and is left undone deliberately - it buys the benchmark nothing.

**Copy the built `.app` somewhere outside the simulator** the moment it exists - that omission is the whole
reason the 2026-07-13 wipe cost bluesky its golden.

## Rebuilding the golden from the app

The 2026-07-13 device-set wipe (see
[golden-simulator.md](golden-simulator.md#the-2026-07-13-device-set-wipe)) destroyed every simulator, and
**Bluesky's `.app` existed only on those simulators** - unlike Element, whose product survives in its
checkout's `build/` dir. That is why the app had to be rebuilt from source at all, and why the built bundle
now lives at `~/apps/Bluesky.app` (+ tarball) on the bench host. Keep it that way.

The stale 2026-07-01 binary is at `~/Bluesky.app.STALE-2026-07-01` - **do not use it.** Its native modules
are older than the JS Metro serves, so it installs and launches but dies on the splash screen with
`ReanimatedError: Mismatch between JavaScript part and native part of Reanimated`. It is kept only as
evidence of that failure mode; the working bundle is the one in `~/apps`.

### The app talks to REAL Bluesky unless you fix `.env` (this is the big one)

Two entries in `~/dev/social-app/.env` ship **empty**, and each empty value silently points the app at
production. Run **`tasks/setup/bluesky/fix_env.py`** (idempotent) and restart Metro with `-c`.

| var | empty means | consequence |
| --- | --- | --- |
| `EXPO_PUBLIC_BLUESKY_PROXY_DID` | falls back to `did:web:api.bsky.app` | the app sets `atproto-proxy: did:web:api.bsky.app#bsky_appview` on every `app.bsky.*` call, and **our own PDS proxies them to the real Bluesky AppView** |
| `GEOLOCATION_DEV_URL` | `"" ?? PROD` is `""`, not `PROD` | geolocation URL becomes `""`, the fetch fails, `countryCode` stays undefined |

The proxy one is vicious, because it never looks like a config problem. It surfaces as three unrelated
"bugs": `getPreferences` returns `MethodNotImplemented` (a PDS method - the AppView genuinely does not
implement it), the dev-env labeler DID comes back `identity unknown` (it does not exist on real Bluesky), and
the Following feed spins forever. Chasing any one of them leads nowhere. `curl` against the local PDS works
the whole time, which is what makes it so confusing - the app is simply not talking to it.

**Leave `EXPO_PUBLIC_CHAT_PROXY_DID` empty.** That is what makes the Chats tab fail with "could not resolve
iss did", which is the expected end state for `bsky-13`.

### Age Assurance blocks the app, and cannot be satisfied

Bluesky 1.122.0 gates the entire app behind an **Age Assurance** screen until it can prove the account is of
age. Setting `personalDetailsPref` / `declaredAgePref` on the local PDS does not help (verified - the gate
stays), because `app.bsky.ageassurance.getState` needs a `countryCode` and the app's server-state prefetch
cannot complete against the self-hosted stack.

The app ships its own escape hatch - `src/ageAssurance/debug.ts` mocks the region config (`access: 'full'`),
a birthdate, and server state (`status: 'assured'`), behind switches literally written as `(IS_DEV && false)`.
**`tasks/setup/bluesky/disable_age_assurance.py`** flips them on for dev builds. Scope is age assurance only;
no bench task involves it.

> Possibly now redundant: this was applied *before* the `EXPO_PUBLIC_BLUESKY_PROXY_DID` fix, when the age
> prefetch was still hitting real Bluesky. With the proxy corrected the app may clear the gate on its own
> from the birthdate on the PDS (the pre-wipe golden had one set, which is evidence it used to). Untested -
> testing it needs a sim boot, which cannot happen while a matrix is running. If you revert it, verify a
> fresh clone still lands on the Following feed.

**A note on `putPreferences`: it REPLACES the whole preferences array.** Sending just a birthdate wipes
`savedFeedsPrefV2` - i.e. the pinned "Following" feed the tasks depend on. Read, merge, then write.

### Rebuilding the golden

1. `make_golden.sh create` the sim - it now **enables the accessibility server** for you. Without it the sim
   exposes an empty AX tree, `argent run describe` returns nothing, and every tap lands nowhere.
2. **Pre-disable the Expo dev-menu onboarding sheet** before first launch, or it covers the app and cannot be
   dismissed (it is AX-opaque, so argent's taps do not reach it):

   ```sh
   xcrun simctl spawn <udid> defaults write xyz.blueskyweb.app EXDevMenuIsOnboardingFinished -bool YES
   ```

   Write it through `simctl spawn defaults` - editing the plist directly does not work, because the sim's
   `cfprefsd` caches the domain and overwrites the file.
3. `simctl install` the app, open the dev-launcher's `localhost:8081` entry, and sign in with hosting
   provider **Custom -> `http://localhost:3000`** as `bench.test` (password in `GET :1987/info`).
4. Back the finished golden up **outside** the device directory (golden-simulator.md).

**A build is safe beside a live matrix; a boot is not.** `reap_stale_clones()` aborts a run on *any* booted
sim that is not its own clone, so do steps 1-3 only when no matrix is running.

**Port `:3000` collides with Mastodon.** The atproto PDS and the local Mastodon instance both want it; they
cannot both be up.
