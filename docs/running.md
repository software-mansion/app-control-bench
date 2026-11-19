# Running the benchmark

## One command

```bash
./run_all.sh
```

OpenCode must be the 2.x version pinned in `configs/opencode-version.json` (2.3.0; a 1.x build is refused,
see [harness.md](harness.md#opencode-2)): `npm install -g @opencode/cli@2.3.0`, or
`curl -fsSL https://opencode.ai/v2/install | bash`, which replaces a 1.x binary in `~/.opencode/bin`.
Existing API keys carry over: OpenCode 2 imports the 1.x `auth.json` the first time it starts.

OpenCode 2 opens the same `~/.local/share/opencode/opencode.db` as 1.x and migrates it in place. On a host
where something else still runs OpenCode 1.x, keep that database out of it: set `OPENCODE_DB` to the absolute
path of a file of the bench's own and install OpenCode 2 beside the 1.x binary (`npm install --prefix <dir>`,
`BENCH_OPENCODE=<dir>/node_modules/.bin/opencode`). The keys are still imported from `auth.json`.

`run_all.sh` needs **zero per-machine editing**. It auto-detects node / opencode / agent-device / argent,
runs a **preflight** (is the lock free? is the golden present and Shutdown? are the keys there? - it fails
*before* spending API money on a broken surface), **self-heals** the ledger, runs every pending unit, judges,
and prints final coverage.

Restrict it with env vars:

| Env | Effect |
| --- | --- |
| `ONLY=haiku55_low,haiku55_high` | only these models. Without it, the current edition's roster (`configs/editions.json`, [editions.md](editions.md)) |
| `TOOLS=argent` | only these tools |
| `APPS=bluesky,element` | only these apps |
| `SKIP_JUDGE=1` | run the matrix but don't score it. Judging is **on** by default - see [judging.md](judging.md) |
| `FORCE=1` | proceed despite a preflight failure |

## Health and coverage, without running anything

```bash
python3 runner/doctor.py                          # preflight + coverage + phantom report (read-only)
python3 runner/doctor.py --models gpt_low --apps bluesky
python3 runner/doctor.py --heal                   # reset phantom-masked gaps
```

`doctor` exits non-zero if the surface is unhealthy or masked gaps remain, so it works as a CI gate. It
checks: environment paths, the device lock, the golden, stale `bench-run-*` clones, foreign booted sims,
that the proxy ports (8788 / 8790 / 8792) are **down**, OpenCode auth, and each app's reset hook (bluesky `:1987`,
element `:8008`, mastodon `:3000`).

## Granular

```bash
python3 runner/bench.py --list                    # sanity: models / tools / tasks
python3 runner/bench.py --verify-golden           # clone the golden, launch each app, screenshot, destroy
python3 runner/run_matrix.py                      # the resumable engine
python3 runner/bench.py --cell gpt_low:argent --task bsky-01
python3 runner/bench.py --all --apps bluesky --models gpt_low,gpt_high
python3 runner/preload.py                         # each tool's host preload now (bench.py does it anyway)
python3 runner/judge.py                           # vision judge over each final.png (resumable)
npm run build                                     # -> public/index.html + public/index-runs.html
```

`bench.py` flags: `--list`, `--cell <model:tool>`, `--task <id>`, `--all`, `--models`, `--tools`, `--apps`,
`--force` (bypass the ledger skip), `--wait-lock` (queue behind another stream), `--verify-golden`.

## The queue and the dashboard

`run_matrix.py` takes its work from `runner/queue.json` - a list of `(model, tool, apps)` entries with an
`enabled` flag and a `priority`. Env overrides (`ONLY` / `TOOLS` / `TASK` / `APPS`) take precedence over the
queue when any of them is set.

```bash
python3 runner/dashboard.py                       # -> http://127.0.0.1:8765
```

The dashboard is the single control surface for what runs next: view and **edit** the queue (enable/disable,
reorder, add/remove - it writes straight back to `queue.json`), plus live telemetry (done / pending /
judged, success rate), observed throughput, estimated completion, and cost (spent so far, summed from each
run's transcript, plus projected for the enabled queue).

Its `success rate` card is **stricter than the report's**: `success / judged`, so a `partial` counts in the
denominator and scores nothing, where the report gives it 0.5. The two numbers are supposed to differ - see
[reporting.md](reporting.md#what-the-report-contains).

## The ledger

`results/ledger.json` tracks each unit through `pending → completed → judged`. It is **derived from disk**
(`ledger.disk_state`): a unit is `pending` if there's no `meta.json`/`final.png` or `returncode == -2`
(a surface error, not a model failure); `judged` if `score.json` holds a valid verdict; `completed`
otherwise.

Two behaviours worth knowing:

- **`needs_run()`, not `has_run()`.** A unit whose only result came from an older harness is *re-run*,
  not skipped. That's what makes a harness bump self-cleaning. It overwrites in place while the tool
  version is unchanged; bump the tool and the rerun lands in the new version's own cell instead, because
  a unit is `(model, variant, task)` and the variant is part of its identity ([`tool-versions.md`](tool-versions.md)).
- **Phantoms.** The ledger can mark a unit `completed`, its result dir later gets purged, and the mark
  survives - so the runner skips it forever and the cell silently tops out below its full task count (90
  with all three enabled apps; `doctor` prints `done/total` per cell). This bit us three times.
  `doctor --heal` detects ledger-says-done-but-no-disk-result units and resets them to pending.

> **Source-of-truth rule:** `doctor --heal` treats the **local `results/` dir as authoritative**. Run it on
> the host that *owns* the results, not one whose ledger was copied in from elsewhere. A mass-heal (>10
> phantoms) is refused without `--heal-force`, precisely because it usually means "this ledger came from
> another machine", not "these results were purged".

## Timeouts, retries, failure modes

- `RUN_TIMEOUT` (default **900 s**) is a per-run wall cap. A run that hits it is recorded as `timed_out`.
- If the agent makes **0 tool calls** and didn't time out, OpenCode is retried up to **3 times** - that's an
  MCP-not-ready flake, not a model result.
- A surface error (the app or the clone failed to come up) writes `returncode: -2` and a
  `SURFACE_ERROR:` stderr tail, which keeps the unit **pending** so it gets retried rather than scored as a
  failure.
- A tool whose host preload fails (`PreloadError`, see [harness.md](harness.md#host-preload-one-time-tool-setup-outside-the-timer))
  runs **none** of its units: they stay pending, the rest of that cell or queue entry is skipped with the
  failing step's output tail, and the other tools carry on.
- `TeardownError`, `ResetError`, and `DeviceError` **abort the matrix**. They mean the machine can no longer
  guarantee isolation, and continuing would produce data we'd have to throw away.

## Concurrency

**One device at a time, no exceptions.** A host-wide `flock` at `/tmp/app-control-bench.device.lock` makes a
second stream fail loudly. Don't run argent tools from anywhere else while the matrix runs - the teardown
would kill them mid-flight (and with `BENCH_KILL_SCOPE=all`, it kills every match, not just its own).

## Env reference

| Var | Default | Purpose |
| --- | --- | --- |
| `RUN_TIMEOUT` | `900` | per-run wall cap (s) |
| `LOAD_WAIT` | `8` | app settle time (s); per-app override in `APPS` |
| `BENCH_LOCK` | `/tmp/app-control-bench.device.lock` | the device lock |
| `BENCH_KILL_SCOPE` | `owned` | `all` on a dedicated host (`run_all.sh` exports `all`) |
| `BENCH_PIDS` | `/tmp/app-control-bench.pids.json` | pid registry for ownership-scoped teardown |
| `BENCH_EFFORT_JSON` | - | set by `isolation.py`, read by the proxies |
| `BENCH_RESET_SOFT` | - | `1` downgrades a reset hard-fail to a warning |
| `BENCH_GOLDEN_REFRESH_S` | `3600` | max age of the Bluesky golden session |
| `BENCH_IGNORE_FOREIGN_SIMS` | - | `1` tolerates a foreign booted sim |
| `BENCH_PRELOAD_SIM` | first available iOS simulator that is neither the golden nor `bench-*` | the simulator agent-device's runner build names as its `-destination`; only resolved, never booted; the golden or a `bench-*` device is refused |
| `ONLY` / `TOOLS` / `TASK` / `APPS` | - | `run_matrix` queue overrides |
| `REGEN_POLLUTED` | - | `1` also regenerates polluted cells not in the queue (real API spend - a deliberate switch) |
| `BENCH_UDID`, `BENCH_NODE`, `BENCH_OPENCODE`, `BENCH_ARGENT`, `BENCH_AGENT_DEVICE`, `BENCH_DEVICE_SET` | auto-detected | machine path overrides (`bench_env.py`) |
| `REPORT_APPS` / `REPORT_MODELS` / `BENCH_PUBLIC` | - | report filters |

## Tests

```bash
python3 runner/test_isolation.py
python3 runner/test_lifecycle.py
python3 runner/test_preload.py
python3 runner/test_ambient.py
python3 runner/test_opencode.py
python3 runner/test_editions.py
cd web && npm test
```

Device-free unit checks. `test_isolation.py` covers `isolation.py`: the lock (second stream refused, released
on death), ownership-scoped teardown (owned processes and their children die; an unregistered match survives
and is reported as a stray), run timeout, reset hooks (hard-fail vs `BENCH_RESET_SOFT`), the `env.json`
round-trip, and provider detection. `test_lifecycle.py` fakes simctl and python3, and covers `run_all.sh`
passing `TOOLS` to doctor, agent-device package resolution through a symlinked binary, `BENCH_DEVICE_SET` on
every harness simctl call, and the refusal of a golden name two simulators carry. `test_preload.py` covers the
preload table and its wiring: build-then-present, once per tool version, every failure fatal and remembered,
the audit fields in `meta.json` / `env.json`, a failed preload leaving its unit pending, the build destination
never being the golden or a `bench-*` device, agent-device's version and bundle-hash guards, and argent's entry
against fake binaries.
`test_editions.py` covers editions: the config and its validation, that the runner, the OpenCode pin, the
judge and the default matrix all agree with the current edition, the edition stamp on a run, the exporter
putting every run in exactly one edition and emitting one dataset per edition, the judge's handling of a
model that rejects `temperature`, and the publish gate reading both result layouts. `npm test` in `web/`
covers the edition URLs.
`test_opencode.py` covers how OpenCode 2 is run: the version guard and pin, the `opencode run` command
line, the `meta.json` stamps, counting the MCP calls inside `execute`, and flagging a run whose MCP tools
were called directly.
`test_ambient.py` builds synthetic hosts and checks each OpenCode host source is neutralised, refused or
let through as [`harness.md`](harness.md#ambient-instructions-nothing-of-the-hosts-reaches-a-run) says,
that a refusal leaves its unit pending and stops `bench.py` / `run_matrix`, and the `env.json` record.
