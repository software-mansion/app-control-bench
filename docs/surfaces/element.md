# Element (Matrix)

**Tasks:** `element-01` … `element-30` (30) · **Bundle:** `im.vector.app` · **Scripts:**
`tasks/setup/element/` · **Status:** ✅ wired into `bench.APPS`

```python
"element": {
    "bundle": "im.vector.app", "open": None,
    "reset_pre": [PY, "tasks/setup/element/seed.py"],
    "reset_timeout": 300,
    "load_wait": 25,
}
```

## The app

**Element Classic** (`element-ios` / Riot **1.11.40**), built from `~/dev/element-ios` (branch `develop`).

**Frozen at** `element-hq/element-ios` @ `36a1788da26933557c468325da2851298ed0386b` (2026-06-23). The version
comes from `Config/AppVersion.xcconfig` (`MARKETING_VERSION = 1.11.40`) and matches the installed
`Element.app` Info.plist. Recorded in `configs/app-versions.json`; `bench.py` warns if the checkout drifts.

**Build with the stable `/Applications/Xcode.app` (iOS 26.5 SDK).** Xcode-beta with the iOS 27 SDK fails on
explicit precompiled modules - that was a long-standing blocker.

The **only** source change is `Config/BuildSettings.swift`:
`serverConfigDefaultHomeserverUrlString` → `http://localhost:8008`.

Product lands at `build/Build/Products/Debug-iphonesimulator/Element.app`. The built artifact survives OS
updates even when the simulator that hosted it is deleted - so a "rebuild" is usually just
`simctl create` + `simctl install`, not an actual Xcode build.

The Matrix session **persists** across `reset_app`'s terminate + relaunch, so one manual login covers the
whole matrix.

## ⚠️ Pollution hazard: FLEX debug overlay (Debug build only)

The `Debug` build links **FLEX 5.22.10** via the Podfile
(`pod 'FLEX', '~> 5.22.10', :configurations => ['Debug']`). FLEX is a full-screen in-app inspector
(`FLEXWindow` overlays the whole app: an object explorer with UIApplication/UIResponder/NSObject tabs, plus a
Network History view reading "0 Requests (Zero KB received)"). Its activation is a global gesture recognizer
that only fires on **real HID touch injection**, so:

- It gets tripped **only on the `argent` tool path** (real coordinate taps/gestures). The `agent-device` path
  drives via accessibility actions and has **never** triggered it (0/60 runs vs ~10% of argent element runs).
- Once open, FLEX is a modal overlay that eats taps and keyboard input. Agents that don't recover get stuck
  in the inspector and fail the task. **Low-effort models are hit hardest** — they trigger it more (erratic
  gestures) and recover from it less. Confirmed derailing e.g. `gpt_low:argent` `element-03` (final screen =
  FLEX Network History) and `element-30` (final screen = FLEX UIApplication explorer).

**This is benchmark pollution, not a model result.** A run whose final screenshot shows the FLEX inspector,
or whose transcript narrates a "debug overlay / inspector" hijacking input, must be regenerated on a
FLEX-free build. **Fix:** build **Release** (drops the FLEX pod entirely) or remove the `FLEX` pod line from
the Podfile and rebuild; keep `MARKETING_VERSION` and the harness stamp unchanged so only the polluted runs
need regenerating.

## The homeserver - Synapse

Local **Synapse** on `http://localhost:8008` (HTTP, `tls: false`, `server_name: localhost`), running from
`~/synapse/` + `~/synapse-venv` on the bench host. iOS ATS is satisfied via `NSAllowsArbitraryLoads`.

`rc_login` is relaxed so login bursts don't hit `M_LIMIT_EXCEEDED`.

```bash
# health
curl http://localhost:8008/_matrix/client/versions

# restart
pkill -f synapse.app.homeserver
cd ~/synapse && nohup ~/synapse-venv/bin/python -m synapse.app.homeserver \
  -c ~/synapse/homeserver.yaml > ~/synapse.log 2>&1 &
```

### Accounts

| User | Password | Role |
| --- | --- | --- |
| `@alice` | `alice-bench-2026` | **the account the app is logged in as** |
| `@bob` | `bob-bench-2026` | DM partner |
| `@carol`, `@dave` | `…-bench-2026` | room members |
| `@seedadmin` | `seedadmin-bench-2026` | used by `seed.py` |

## The seed

`tasks/setup/element/seed.py` is idempotent: it **purges all rooms and recreates the exact scenario** (users
persist). It has 429 / `M_LIMIT_EXCEEDED` retry logic. `verify.py` logs in as alice and prints joined rooms,
`m.direct`, and per-room name / member count / tags.

Rooms: **Team Standup**, **Weekend Plans**, **Book Club**, **Project Phoenix** - plus a real 1:1 DM with Bob
(`m.direct` on both sides), the Space **Benchmark HQ**, and unread state (last events from non-alice users).
Rooms are **unencrypted**.

Task-specific fixtures: an "update" message in Team Standup (`element-22` search), messages to react to
(`element-23`), messages to reply to in Book Club (`element-09`/`24`), 4 members in Project Phoenix
(`element-15`), Team Standup tagged `m.favourite` (`element-11`), the Bob DM (`element-02`/`29`).

Because the seed runs as `reset_pre` before every run, the mutating tasks (`03`, `08`, `23`, `24`, `28`,
`29`, `30`) cannot leak into the next run.

> ### The critical invariant
> `seed.py` resets alice's password with **`logout_devices: False`** - **on purpose**. That is what lets a
> reseed run before every single run without logging the app out. If it is ever flipped to `True`, every
> reseed destroys the golden's session and you will need to re-golden. Verified: after a reseed, alice's
> token still returns 200 from `whoami`.

## One-time cross-signing

On a **fresh login** (or any re-login after a logout), Element shows a "Verify this device" screen. Tap
**"Reset everything"** → Reset → alice's password → Got it. The seeded rooms are unencrypted, so this loses
nothing, and it clears the persistent verify banner. With `logout_devices: False` you only ever do this once,
while building the golden.

(Tapping "Skip" instead leaves the banner on the room list. It is non-blocking and near-irrelevant to the
vision judge, since in-room screenshots don't show it - but "Reset everything" is cleaner.)

## Task coverage

All 30 were **audited on-device against the live UI** (2026-07-06).

- **Verified working:** 01-05, 07-15, 20-25, 27-30.
- **High confidence** (gesture verified): 06, 26.
- **Reachable but nested:** `16` Notifications, `17` Security & Privacy, `18` Sessions, `19` Help/About are
  **sections of one long Settings page**, not distinct screens. The vision judge accepts the section; a
  stricter judge would not.

Backend/UI limits reflected in the task criteria: the create menu has **no "Join Room"** (`element-06`); the
account menu shows **no display name or user ID** (`element-20`); there is no separate "Help & About" screen -
the version lives in the Settings ABOUT section (`element-19`).

`11`/`12`/`13` were **rewritten** - see [`../tasks.md`](../tasks.md#task-history-worth-knowing).

## History: the tooling blocker that killed a whole batch

Worth reading before you trust any old element data.

For a while **neither tool could drive Element**, and the runs produced ~0% success - a *tooling* artifact,
not a model signal:

- **argent** exposed **zero device-control tools** when Element was foreground. The model would report "no
  device-control tools available" and stop after ~8 s. argent worked fine on Bluesky in the same session.
- **agent-device** foregrounded Element via a **URL scheme**, which triggered iOS's *"Open in 'Element
  Classic'?"* universal-link dialog. That dialog blocked session establishment, so most tool calls returned
  `Expected stateDir to be a non-empty string`.

The dialog also **landed in `final.png` even when the agent never snapshotted it**, so grepping transcripts
undercounted the damage (6 hits vs 64 real). A vision pass over all 122 element screenshots found **64 of
122 runs polluted**. They were purged.

**Resolved** by a fresh simulator plus foregrounding through **bundle launch** (`open im.vector.app` /
`simctl launch`) rather than URL-scheme `openurl`. `bench.py`'s `reset_app` uses `simctl launch`
(`"open": None`) for exactly this reason. A clean re-run then produced 32 tool calls, 0 errors, rc=0, no
dialog. So the degradation was tied to the old sim's polluted state, not a fundamental agent-device bug.

The lesson that generalizes: **an unanswered iOS system dialog is invisible to transcript analysis and
silently contaminates the only artifact the judge sees.** Answer every one-time dialog while building the
golden.
