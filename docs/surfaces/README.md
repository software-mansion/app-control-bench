# Surfaces (the apps under test)

A "surface" is a **real native iOS app** plus the backend it talks to. Every surface must be **reproducible
and frozen**: a pinned backend, a deterministically seeded dataset, a logged-in account that survives a
relaunch, and a pinned app build. If the surface drifts, the scores are not comparable across runs.

v1 of this benchmark drove **web apps in Mobile Safari**. That was wrong - a browser is not an app, and it
measured web-AX quality rather than device control. Every surface here is native.

## Status

| Surface | Tasks | App | Backend | Wired into `bench.APPS`? |
| --- | --- | --- | --- | --- |
| [Bluesky](bluesky.md) | 30 | Bluesky Expo dev-client (`xyz.blueskyweb.app`) | self-hosted atproto (`:1987` control) | ✅ enabled |
| [Element](element.md) | 30 | Element Classic (`im.vector.app`) | self-hosted Synapse `:8008` | ✅ enabled |
| [Mastodon](mastodon.md) | 30 | IceCubes (`com.example.IceCubesApp`) | local Mastodon `:3000` | ✅ enabled |
| [Immich](immich.md) | 30 | Immich (`app.futo.immich.debug`) | self-hosted Immich `:2283` | ❌ commented out |
| [Expensify](expensify.md) | 30 | Expensify (native) | production API | ❌ commented out - notes only |

[**Golden simulator**](golden-simulator.md) - the shared, logged-in simulator image every run clones. Read
this before touching any surface: it is where the apps are installed and logged in, and it is the reason a
run can be isolated at all.

## The two kinds of reset

Isolation needs both, and each surface must declare what it uses:

1. **App state** - handled generically by cloning the golden simulator per run and destroying the clone
   afterwards. No surface has to do anything for this.
2. **Server state** - handled per-app by **reset hooks** in `bench.APPS`:
   - `reset_pre` runs **before the clone boots** (element and mastodon re-run their `seed.py`; bluesky
     `POST :1987/reset`).
   - `reset_post` runs **after the run** (bluesky resets again).

### Reset hook contract

- **Idempotent.** It will be run hundreds of times.
- **Must never invalidate a stored session.** If a reseed logs the app out, the golden is dead and you have
  to re-golden. This is why Element's `seed.py` resets Alice's password with `logout_devices: False` - flip
  that to `True` and every reseed destroys the golden.
- **Exit 0 on success. A nonzero exit aborts the run** (hard-fail by default; `BENCH_RESET_SOFT=1`
  downgrades it to a warning). Better to stop the matrix than to score a dirty surface.
- stdout/stderr are captured to the run dir (`reset-server.log`).

## Adding a surface

Use [`immich.md`](immich.md) as the template - it is the most completely built-out. You need:

1. A pinned backend (digest-pinned compose file, `bring-up.sh`, `verify.sh`, `reset.sh`).
2. A deterministic seed (fixed RNG seed; the same dataset every time).
3. A native app build, pinned, with the bundle id recorded.
4. A task → data coverage matrix: every one of the 30 tasks must have the data it needs to be solvable.
5. The app installed and logged in **in the golden**.
6. An entry in `bench.APPS` with its bundle id and reset hooks.

Scripts stay in `tasks/setup/<app>/`; the documentation is here.
