# The golden simulator

**Scripts:** `tasks/setup/golden/make_golden.sh`  ·  **Manifest:** `configs/golden.json`

## Why

One fully-provisioned, logged-in simulator that the benchmark **never runs on**. Every run does a
`simctl clone` of it - which copies the data container byte-for-byte, keychain and app storage included -
runs the task on the clone, and deletes the clone.

That buys three things:

- **Byte-identical start state** for every run. A task cannot be made easier by what the previous run left
  behind.
- **No ordering effects.** Run the matrix in any order and get the same answer.
- **One manual login, ever.** Logging into Bluesky/Element/Mastodon by hand once, in the golden, covers the
  entire matrix.

## Naming and the trust contract

| Name | Meaning |
| --- | --- |
| `bench-golden-staging` | A golden under construction. Mutable, hands-on. |
| `bench-golden-v<N>` | A finalized golden. **Must always be `Shutdown`.** |
| `bench-run-<ts>-<task>` | A throwaway per-run clone. Reaped if it survives a crash. |
| `bench-golden-smoketest` | A throwaway clone `finalize` creates and deletes. |

**A golden found `Booted` is no longer trusted.** Something ran on it, so its state is no longer the state
that produced the existing results. Re-golden as `v<N+1>`. `sim_device.check_golden()` enforces this at the
start of every run.

The **only sanctioned boot of a golden** is `sim_device.refresh_golden_sessions()` - see the Bluesky caveat
below.

## Building one

> Not on a shared dev machine. `make_golden.sh` creates simulators directly; on a shared machine all
> simulator creation must go through the device allocator. Build goldens on the dedicated bench host.

```bash
# fast path - clone an already-logged-in sim
tasks/setup/golden/make_golden.sh from-existing <udid-or-name>

# full path
tasks/setup/golden/make_golden.sh create "iPhone 16" "com.apple.CoreSimulator.SimRuntime.iOS-18-5"

tasks/setup/golden/make_golden.sh finalize v2      # smoke-test, rename staging -> bench-golden-v2
tasks/setup/golden/make_golden.sh refresh-check    # are all goldens safely Shutdown?
```

`finalize` boots a *clone*, checks `get_app_container` for each bundle, screenshots, deletes the clone, and
renames staging. It **never writes `configs/golden.json`** - point the manifest at the new name yourself.

### Manual checklist during `create`

- Install and log into **Bluesky** (`xyz.blueskyweb.app`) - tap **Open** once on the deep-link dialog.
- Install and log into **Element** (`im.vector.app`) as `@alice` - do the one-time cross-signing
  ("Reset everything" → Reset → password → Got it; the seeded rooms are unencrypted, so nothing is lost).
- Install and log into **IceCubes** (`com.example.IceCubesApp`) against local Mastodon.
- **Answer every one-time dialog** - notification permission, save-password, analytics. Anything you leave
  unanswered will pop up mid-run in every clone and contaminate the screenshot.
- **Run one throwaway agent-device session**, so its XCTest automation runner gets installed into the
  golden. Otherwise the first tool call of every run pays to install it.
- **Install Mobile MCP's agent**: `mobilecli agent install --device <udid>`. Otherwise the first mobile-mcp
  tool call of every run downloads it from GitHub and installs it (about 5 s, and a network dependency).
  Do both again whenever the tool's pinned version changes: an older runner or agent is replaced inside
  the run.
- After verifying Bluesky is at rest, record `bsky_at_rest_ts` in `configs/golden.json`.

`make_golden.sh` safety contract: it refuses to run while any `bench-golden-*` sim is Booted, and only ever
touches sims named `bench-golden-staging` / `bench-golden-v*` / `bench-golden-smoketest` plus an explicit
`from-existing` source.

## The manifest

`configs/golden.json` resolves the golden **by name**, because UDIDs differ per machine. A name carried by
more than one simulator is refused rather than guessed. Mutable per-host stamps (`session_refreshed_ts`)
live in the gitignored `configs/golden-state.json`.

The harness looks the golden up, clones it and drives the clone in the `BENCH_DEVICE_SET` device set (default:
`~/Library/Developer/CoreSimulator/Devices`). `make_golden.sh` does not read `BENCH_DEVICE_SET`: it always
builds in the default set. The `none` baseline's own `xcrun simctl`, typed by the agent without `--set`, also
targets the default set, and argent and mobile-mcp are not given the set either. So under a non-default
`BENCH_DEVICE_SET` only agent-device reaches the clone: argent, mobile-mcp and `none` runs fail inside the
agent and score as model failures.

> **The committed `configs/golden.json` is a stub** (`"name": "bench-golden-v1"`, `created_ts: 0`,
> `bsky_at_rest_ts: 0`, `"NOT BUILT YET"`). The real goldens exist only on the bench host and are named
> `bench-golden-bsky` and `bench-element` - which is what the shipped `meta.json` files record. Do not trust
> the tracked file to tell you what actually ran; trust `meta.json.golden`.

Bump `name` to `bench-golden-v<N+1>` on every re-golden and record what changed in `notes`.

## When to re-golden

- An app was rebuilt. **Prefer in-place:** clone golden → staging, `xcrun simctl install` over the top (this
  keeps the data container, so logins survive), then finalize as `v<N+1>`.
- The golden was found Booted.
- Bluesky sessions went stale (below).
- A new app joins, or you need a newer iOS runtime.

## Provisioning step nobody wrote down: enable the accessibility server

**A freshly created simulator has no accessibility server, so argent's `describe` returns an EMPTY tree and
its taps land nowhere.** Every golden that works has had this enabled; a brand-new one has not, and the
failure looks like "argent is broken" rather than "the sim is unprovisioned". Do this before installing apps
(`make_golden.sh create` now does it for you):

```sh
xcrun simctl bootstatus <udid> -b                          # spawn only works on a BOOTED device...
xcrun simctl spawn <udid> defaults write com.apple.Accessibility ApplicationAccessibilityEnabled -int 1
xcrun simctl spawn <udid> defaults write com.apple.Accessibility AccessibilityEnabled -int 1
xcrun simctl shutdown <udid> && xcrun simctl boot <udid>   # ...but the AX server only reads it at boot
```

Chicken-and-egg worth stating plainly: `simctl spawn defaults` fails on a shut-down device
(`Process spawn via launchd failed because device is not booted`), yet the setting only takes effect at
startup - so it is boot, write, **bounce**. Writing it before the first boot cannot work.

Verify: `argent run describe --udid <udid>` on the home screen must list ~20+ elements, not zero.

## Never delete a simulator a golden was cloned from

A clone keeps the app records of its source: `xcrun simctl listapps <clone>` shows each app's `Path` inside
the device the app was **installed** on, however many clones ago. Delete that device and the app can no
longer be launched in any descendant ("The request was denied by service delegate"). On 2026-11-17 this broke
Mobile MCP's agent in a new Bluesky golden after the intermediate clone it had been installed on was deleted.
Reinstalling the app on the new golden moves the record there. Check `listapps` before deleting any `bench-*`
simulator; the current Bluesky golden's `Bluesky.app` still points into the original `bench-golden-bsky`.

## Back the goldens up OUTSIDE the device directory

A golden that exists only as `~/Library/Developer/CoreSimulator/Devices/<udid>/` is one bad command away from
being unrecoverable - and on 2026-07-13 **every simulator on radoniusz was destroyed**, goldens included
(below). There is no Time Machine on that host and APFS only keeps OS-update snapshots, so there was nothing
to restore from.

```sh
# with the golden Shutdown (cloning/reading it is safe)
mkdir -p ~/golden-backups
tar -czf ~/golden-backups/<name>.tar.gz -C ~/Library/Developer/CoreSimulator/Devices <udid>
# restore: untar into the Devices dir, then `xcrun simctl list` picks it up
```

**Also keep the built `.app` bundles outside the sim.** `Element.app` survives at
`~/dev/element-ios/build/Build/Products/Debug-iphonesimulator/Element.app`, which is why the element golden
could be rebuilt in half an hour. Bluesky's binary lived *only* on the simulators, so when they went, the app
had to be rebuilt from source - four separate Xcode-26 defects deep (see
[bluesky.md](bluesky.md#building-the-app-four-repairs-in-order)). It now lives at `~/apps/Bluesky.app` and
`~/apps/Bluesky-1.122.0-debug-arm64.tar.gz`. Keep it there.

## The 2026-07-13 device-set wipe

**Every simulator on the host was deleted**, both goldens with them. The chain:

1. A `none`-baseline agent (`haiku_high/none/bsky-06`) went around `simctl` entirely and reached into the
   **filesystem**: it read another device's app container (mmkv, HTTPStorages sqlite, preferences) and then ran
   `rm -rf ".../Devices/<other-udid>/data/Containers/Data/Application/<app>/"*`.
2. Corrupting a live device's internals under a running CoreSimulatorService, followed by the crash-loop
   handler's `killall -9 com.apple.CoreSimulator.CoreSimulatorService`, took the whole device set with it.
   Afterwards `Devices/` held only `device_set.plist` - every device directory was gone.

**The `simctl` guard did not stop this.** `bench_env.none_shim_dir(udid)` scopes `xcrun simctl` to the run's
own clone, but a shim only shadows a *command name*, and a `none` agent has a **shell** - simulators are just
directories owned by the same user, so `rm`, `python` and `mv` walk straight around it.

**Now closed: `bench_env.sandbox_wrap()`** runs the `none` agent under `sandbox-exec`, denying it writes to
the device set (except its own clone) and to `~/golden-backups` / `~/apps`. Reads stay allowed - the point is
to stop destruction, not to hide the host - and `simctl` still works on its own device, because the writes
are done by CoreSimulatorService over XPC, not by the agent process. Verified on the bench host: the exact
`rm -rf` above is refused and the golden survives it, while `simctl list` is unaffected.

Two traps if you touch this:

- **The profile matches CANONICAL paths.** A `subpath` naming a symlinked parent (`/var` -> `/private/var`
  is the classic) silently matches nothing, and the rule is a dead no-op that still *looks* right.
- **Do not deny `results/`.** The agent's stdout *is* the run transcript, which lives there.

The backups above remain the last line of defence, and the reason a wipe is now a 10-minute restore.

## Known blocker: the Bluesky rotating-refresh-token caveat

AT Protocol uses **rotating refresh tokens**. The golden stores one fixed session snapshot. The first clone
whose app refreshes its session rotates the token server-side, which makes the golden's stored copy stale -
and eventually new clones boot into a **logged-out** Bluesky.

Mitigation: `sim_device.refresh_golden_sessions()` periodically (and before a matrix) boots the golden
itself in a controlled window while no clones are running, lets the app refresh and persist a session, then
shuts it down. `BENCH_GOLDEN_REFRESH_S` (default 3600) is the max session age. **This controlled boot is the
only time a golden is legitimately Booted.**

Local Synapse and Mastodon sessions do not expire, so this is Bluesky-specific.

### What actually blew it up (2026-07-13)

The mitigation only holds if nothing *else* rotates the token. On 2026-07-13 a `none`-baseline agent
(`haiku_low/none/bsky-07`) ran, from its shell:

```
xcrun simctl boot   <golden-udid>          # the golden, not its own clone
xcrun simctl launch <golden-udid> xyz.blueskyweb.app
```

Launching the app on the golden refreshed and rotated its frozen `refreshJwt` **outside** the controlled
window. The golden's session died, and because every run clones the golden, later clones began booting into
a logged-out Bluesky - which silently zeroes every run that follows. It also left the golden Booted, which
tripped `reap_stale_clones()` ("foreign booted simulator") and `check_golden()` ("no longer a trusted
template"), aborting `run_matrix` and putting the supervisor into a crash-loop.

The golden was rebuilt by re-logging in through the GUI (see below) and **`bench_env.none_shim_dir(udid)`
now scopes the baseline's `xcrun simctl` to the run's own clone**, so an agent can no longer touch the
golden. The old logged-out golden is kept as `bench-golden-bsky-LOGGEDOUT-BAK`.

### Runs made during a logged-out window do NOT re-run themselves

The damage outlives the golden. Every run produced while the golden was logged out gets stamped with the
**current** harness, so `ledger.needs_run()` sees a completed, current-harness result and skips it forever -
the judge scores it `fail` (the agent was staring at the Create-account screen), and that bogus zero is
load-bearing in the report until someone physically removes it. Fixing the golden does not fix them.

After any logged-out incident, find them and take them out of `results/`:

```sh
# a fail whose reason describes the sign-in / welcome / create-account screen, on a bluesky task
# move (never delete) - a missing meta.json makes run_harness() None, so needs_run() goes True again
mv results/<cell>/<task> ~/bench-quarantine/loggedout-<date>/<cell>/<task>
```

Then bounce `run_matrix` so it re-plans. 14 such runs were found on 2026-07-14 (13 of them in `none` cells,
i.e. squarely on the headline baseline); they are kept in `~/bench-quarantine/` on the bench host and in
`app-control-bench-quarantine/` locally.

**Do not over-correct.** A `none` fail whose final screenshot shows the app behind an **"Open in Bluesky?"**
dialog is a REAL result, not pollution: the no-tool agent navigates with `simctl openurl`, which raises that
system modal, and a weak agent has no way to dismiss it. That is the baseline's genuine ceiling and must be
left in.

**Re-logging in the Bluesky golden without a human** (what the runbook calls a hands-on ritual): clone the
golden, boot the clone, and drive the sign-in with the argent CLI (`argent run describe|gesture-tap|keyboard
--udid <clone>`). Hosting provider must be switched to **Custom -> `http://localhost:3000`** (the self-hosted
PDS) before entering `bench.test` / the password from `GET :1987/info` on the bench host.
Then shut the clone down and rename it over the golden. Verify with `python3 runner/bench.py --verify-golden`
plus an eyeball of the screenshot: it must show the Following feed, not the sign-in landing page.

## Known blocker: a corrupt Element crypto store poisons every clone

If the golden's Element install has a corrupted E2EE crypto store, it **crashes at launch**
(`MXCryptoV2Factory.buildCrypto` → `fatalError`), and since every clone inherits the data container, *every
clone* crashes or boots logged-out. This happened once, caused by accumulated agent activity plus repeated
reseeds against the source sim.

The fix - now a step in the golden runbook for Element:

1. Copy the `Element.app` bundle out.
2. `xcrun simctl uninstall <udid> im.vector.app` - this wipes the corrupt crypto store.
3. `xcrun simctl install` it back.
4. Launch, log in fresh as alice against `localhost:8008`, and answer every one-time dialog.

Diagnosis note: a clone booting logged-out is **not** evidence that cloning is broken. Check the *source*
sim first.
