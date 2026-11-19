# Troubleshooting

Start with `python3 runner/doctor.py` - it checks most of what follows, read-only, before you spend anything.

## The matrix aborts immediately

| Symptom | Cause | Fix |
| --- | --- | --- |
| `another bench stream holds the device lock` | Another run is live - **by design**, one device at a time. | Wait, or `--wait-lock` to queue. If nothing is actually running, a crashed run left the lock: check `lock_holder()`, then clear `/tmp/app-control-bench.device.lock`. |
| `TeardownError` | A bench process survived the kill sequence, or a proxy port is still listening. The machine cannot guarantee isolation. | Find the survivor (`pgrep -fl opencode\|argent\|agent-device`), kill it. Don't run argent tools from another shell during a matrix. |
| `ResetError` | A server reset hook failed - the backend is down or unhealthy. | Fix the backend (`:1987/info`, `:8008`, `:3000`). `BENCH_RESET_SOFT=1` downgrades it to a warning, but you are then scoring a dirty surface. |
| `DeviceError` on the golden | The golden is missing, **Booted**, or its name is carried by more than one simulator. | A booted golden is no longer trusted - see [golden-simulator](surfaces/golden-simulator.md). For a duplicate name, rename whichever simulator is not the real golden. |

## A cell silently tops out below its full task count

This is the **phantom-ledger bug**, and it has bitten three times. The ledger marks a unit `completed`, the
result dir is later purged, but the mark survives - so the runner skips it forever.

```bash
python3 runner/doctor.py            # reports phantoms
python3 runner/doctor.py --heal     # resets them to pending
```

A mass-heal (>10) is **refused** without `--heal-force`, because that many phantoms almost always means the
ledger was copied in from another machine rather than that results were purged locally. `--heal` treats the
**local `results/` dir as the source of truth** - run it on the host that owns the results.

## Runs finish suspiciously fast with 0 tool calls

- **Any model:** if the agent made 0 tool calls and did not time out, `bench.py` already retries up to 3
  times - this is a known MCP-not-ready flake. If it persists, the MCP server isn't starting; check the
  staged config.
- **Local silver models specifically:** a known unfixed bug - they can record 0 tool calls and ~2 s per
  task, never driving the tools at all. This is what produced the all-zero `silver-v10rsn2` runs. It is not
  a model result.

## An `argent` cell's `tool_names` are all `bash`, or `effort_injected` is 0

Both are the same failure: **opencode did not adopt the per-run config dir**, so it ran with whatever
config the launcher's directory has - no `mcp.argent` (the model finds the `argent` binary on PATH and
shells out to the CLI, which is a different surface from the MCP one being measured) and no proxy
`baseURL` (the request goes straight to the vendor, so the cell's `EFFORT` never reaches the model and
`_low` and `_high` become one configuration under two names). Nothing else shows it: `rc` is 0, the tool
calls happen, the agent often solves the task, and the screenshot looks right.

**Cause:** opencode picks its project directory from **`$PWD`**, not from the process's actual working
directory, and `subprocess.Popen(cwd=...)` does not update `$PWD`. `isolation.run_opencode` pins
`PWD=cwd` for exactly this reason - don't remove it. Confirmed by measurement:

| invocation | opencode's directory | proxy hits |
| --- | --- | --- |
| `cd $D && opencode …` | `$D` | 1 |
| `Popen(cwd=$D)` | the launcher's dir | **0** |
| `Popen(cwd=$D, env PWD=$D)` | `$D` | 1 |

`meta.json` carries `effort_injected` so this is checkable after the fact, and `bench.py` prints a loud
warning when a cell with a non-empty `EFFORT` records zero. **A run with `effort_injected: 0` on an
effort-bearing cell is not a result** - delete it and re-run.

## Every verdict is `partial`

Suspect the **task criteria, not the models.** Two causes, both in `tasks.json`. Either the task's
`solved_screen` demands a state the `prompt` never asked for (an extra tap, an opened sheet, a toast that has
already auto-dismissed) - so an agent that obeyed the prompt exactly is marked down - or it describes a screen
the self-hosted backend simply cannot produce. Read the task's `prompt` and `solved_screen` side by side
before blaming a model. See [tasks.md](tasks.md#writing-a-task-solved_screen-must-match-the-prompt-and-both-must-match-reality).

## Results look right but score 0, or aren't scored at all

Check `meta.json` → `versions.harness`. If it isn't the current `bench.HARNESS`, the run is **polluted**:
excluded from every aggregate (a Tool comparison cell with nothing current shows it, marked stale), rendered
as a `stale` chip, and skipped by the judge. It will be re-run and
overwritten. This is intended behaviour after a harness change, not a bug.

## The agent has skills it shouldn't

If a run's transcript shows the agent using `pptx` or `runpodctl` or any of your personal skills, the
**ambient suppression** failed. The run's `opencode.json` must carry the template's `plugins` list
(`"-opencode.config.compatibility"` drops the `~/.claude` / `~/.agents` skill loader), and `bench.py` must run
it under `ambient.RUN_ENV`. Check what OpenCode loads for a staged config:

```bash
cd <staged-cfg-dir> && opencode debug config --standalone   # every config document and directory it reads
```

and the run's `env.json` → `ambient`, which records the host sources found and how each was handled.

Any run with leaked ambient skills is pollution - it measured the operator's laptop, not the tool.
The switches cover `.claude` and `.agents` only; skills under `~/.config/opencode/` or a `.opencode/` above
the run dir have none, so the ambient check refuses the run instead (next section).

## A run refuses with `AmbientError`

The host holds something OpenCode would load into every run and no OpenCode switch keeps out: a
`~/.config/opencode/AGENTS.md`, host agents / modes / skills, a behaviour-bearing key in the global
`opencode.json`, and so on. The error names each file and what to do; `python3 runner/doctor.py --preflight`
lists the same under `== AMBIENT ==`. Move them off the bench host (or aside while it runs). Units stay
pending, so the next pass picks them up. What is and is not refused, and why:
[`harness.md`](harness.md#ambient-instructions-nothing-of-the-hosts-reaches-a-run).

## Screenshot shows an iOS system dialog

An unanswered one-time dialog ("Open in X?", notification permission, save password, analytics) lands in
`final.png` **even when the agent never interacted with it**, and it contaminates the only artifact the
judge sees. Grepping transcripts will *undercount* this badly - a vision pass over the screenshots is the
only reliable detection.

Fix: answer every one-time dialog while building the golden. And foreground apps via **bundle launch**, never
URL-scheme `openurl` - the latter triggers the universal-link dialog. See the
[Element history](surfaces/element.md#history-the-tooling-blocker-that-killed-a-whole-batch).

## Bluesky boots logged out

The **rotating refresh token** problem. The golden's stored session went stale after a clone rotated it
server-side. Run `sim_device.refresh_golden_sessions()`, or re-golden. See
[golden-simulator](surfaces/golden-simulator.md#known-blocker-the-bluesky-rotating-refresh-token-caveat).

## Bluesky shows the dev-client launcher, not Bluesky

Metro isn't serving. Something else is squatting `:8081`, and `yarn start` skipped the dev server *silently*:

```bash
lsof -nP -iTCP:8081 -sTCP:LISTEN     # free it first
cd ~/dev/social-app && yarn start
```

## Element crashes at launch on every clone

The golden's Element install has a **corrupt E2EE crypto store**, and every clone inherits it. Do not blame
the clone. Uninstall / reinstall `im.vector.app` in the golden and log in fresh - see
[golden-simulator](surfaces/golden-simulator.md#known-blocker-a-corrupt-element-crypto-store-poisons-every-clone).

## Every argent gesture fails: `simulator-server exited before becoming ready`

The tool-server log (`~/.argent/tool-server.log`) names the real cause a line earlier:

```
Error: SimulatorKit could not be loaded (Xcode search path: /Applications/Xcode-beta.app)
Error: Failed to initialize SimulatorControl
```

**Xcode 27 moved `SimulatorKit.framework`** from `Contents/Developer/Library/PrivateFrameworks/` to
`Contents/SharedFrameworks/`, and argent only looks in the old place. It picks its Xcode with
`-[NSWorkspace URLForApplicationWithBundleIdentifier:@"com.apple.dt.Xcode"]`, so **LaunchServices decides**,
and LaunchServices answers with the highest `CFBundleVersion` installed - the beta. Every `gesture-*` and
`screenshot` call then fails while `describe` keeps working (that one goes through `ax-service`, which does
not need SimulatorKit), which makes it look like a device problem rather than a toolchain one.

Neither `xcode-select -p` nor `DEVELOPER_DIR` changes the outcome - both were measured against the search
path in the log and neither is consulted. Unregister the beta from LaunchServices:

```bash
LSREG=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Support/lsregister
$LSREG -u /Applications/Xcode-beta.app
```

That holds until LaunchServices rescans `/Applications`, which re-registers it - so **re-run the unregister
before each matrix**, or remove the beta (needs root; `mv` out of `/Applications` is refused by App
Management even for an admin). Renaming it in place does *not* work: LaunchServices tracks the bundle, not
the path, and re-registers it under the new name.

## Environment gotchas

- **`grep` is shadowed on radoniusz** - use `/usr/bin/grep`.
- **`:3000` is contested** - the Bluesky atproto PDS and the local Mastodon instance both want it.
- **argent 0.14.0 is broken on macOS 27.** Use ≥ 0.15.0. Pre-0.15.0 argent runs are harness-validation data,
  not scores.
- **opencode auto-updates itself, and it is part of the measurement.** It is the harness every cell shares,
  so a version that changes mid-roster makes models incomparable. The banked SoTA runs are on **1.17.13**;
  export `OPENCODE_DISABLE_AUTOUPDATE=1` for a long pass, and check `opencode --version` before starting
  (the previous binary is kept beside it as `~/.opencode/bin/opencode-<ver>.bak`).
- **agent-device snapshots are slow** (~27 s cold). A stalled cell is usually a session bind or a slow
  snapshot; the timeout handles it and the run is recorded as `timed_out`.
- **API spend** - gpt / haiku / opus are real money. radoniusz's *compute* is free; the model calls are not.
