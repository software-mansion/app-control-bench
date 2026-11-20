# Architecture

## What the benchmark measures

One agent, one task, one native iOS app, one device-control tool. We vary the model and the tool, hold
everything else constant, and score the final screen.

```
  models (bench_models.MODELS)      ×   tools (bench.TOOLS)    ×   tasks (tasks/tasks.json)
  gpt-5.4-mini {none,low,med,high}      argent       (MCP)         bsky-01..30     ┐ enabled
  gpt-5.5 (med)                         agent-device (CLI)         element-01..30  │ in
  haiku-4.5 {none,low,med,high}         none         (shell)       mast-01..30     ┘ bench.APPS
  opus-4.8 (med)                                                   immich-01..30   ┐ not wired
                                                                   expe-01..30     ┘ (commented out)
```

The headline deliverable is **argent vs agent-device**, broken down by model and by app. Every task also
carries a **navigation category**, stamped into each run's `meta.json` - that is the intended third axis, but
`report.py` does not render it yet. The `none` cell is the control: the same agent with no device-control
tool at all, so the report can state how much the specialised tools actually add rather than just ranking
them against each other.

The harness is **OpenCode** (`opencode run --format json`), used **untampered** - full system prompt, full
tool catalog. Shrinking the harness to make a tool look good would invalidate the comparison; see
[`harness.md`](harness.md) for the rules that keep the cells comparable.

## Where the code lives

| Module | Role |
| --- | --- |
| `runner/bench.py` | The runner. `MODELS` / `TOOLS` / `APPS` / `EFFORT`, `run_one()` (one unit), the CLI. |
| `runner/bench_models.py` | Report-side roster: `MODELS`, `LABELS`, `INTERNAL_MODELS`. Regenerated, narrowed, on every release branch. |
| `runner/bench_env.py` | Machine-specific paths (node, opencode, argent, agent-device, mobile-mcp, device set). Env override → auto-detect → fallback. |
| `runner/gen_configs.py` | Builds the per-run OpenCode config dir and stages each tool's real shipped skills. |
| `runner/isolation.py` | Device lock, verified process teardown, per-run effort proxies, reset hooks, `env.json`. |
| `runner/sim_device.py` | Golden-simulator lifecycle: check / clone / boot / destroy / reap. |
| `runner/ambient.py` | What OpenCode would load into a run from the host (instructions, config, agents, skills, plugins): `RUN_ENV` keeps out what has a switch, `check()` refuses the rest. |
| `runner/preload.py` | The `PRELOAD` table: each tool's one-time host setup, done before the timer. `preload-agent-device.mjs` and `preload-argent.py` are agent-device's and argent's entries. |
| `runner/ledger.py` | Idempotent unit ledger (`pending → completed → judged`) + phantom detection. Owns the variant id and the results layout. |
| `runner/run_matrix.py` | Batch driver over `runner/queue.json` (or env overrides). |
| `runner/judge.py` | Vision scoring pass over each `final.png` → `score.json`. |
| `runner/report_data.py` | Exports normalized, gated, redacted JSON + screenshots to `public/`. |
| `web/` | Vite + Preact + TypeScript report and run explorer; `web/src/shared/metrics.ts` is the one aggregation. |
| `runner/doctor.py` | Preflight health + coverage + phantom heal. |
| (preview) | `npm run dev` — Vite dev server with hot module replacement. |
| `runner/migrate_variants.py` | Moves a results tree onto the variant layout. Idempotent, one-time per tree. |
| `runner/test_isolation.py` | Device-free unit checks for `isolation.py`. |
| `runner/test_ledger.py` | Device-free unit checks for the ledger's variant layout. |
| `runner/test_preload.py` | Device-free unit checks for `preload.py` and its wiring into `run_one` / `run_matrix`. |
| `runner/test_ambient.py` | Device-free unit checks for `ambient.py` against synthetic hosts, and its wiring into `run_one` / `run_matrix` / `doctor`. |

## A unit and its result

The atom of work is a **unit** = `(model, variant, task)`, where a variant is one version of one
toolkit (`argent@0.15.0` — [`tool-versions.md`](tool-versions.md)). It writes to
`results/editions/<edition>/<model>__<variant>/<task>/`, where the edition is the frozen setup the run
belongs to and may be compared within ([`editions.md`](editions.md)):

| File | Written by | Contents |
| --- | --- | --- |
| `meta.json` | `bench.py` | cell, task, app, `nav_category`, `returncode`, `timed_out`, `wall_s`, `provider_retries`, `provider_retry_s`, `n_tool_calls`, `tool_names`, `golden`, `clone_udid`, `versions`, `effort_injected` (requests the effort proxy actually rewrote - `0` on an effort-bearing cell means the run answered at the model's default and is not a result for that cell), `mcp_direct_calls` (calls to the tool's MCP server outside code mode - must be `0`, [harness.md](harness.md)), `unknown_tool_calls` (calls OpenCode refused because no such tool exists) |
| `transcript.jsonl` | OpenCode | the full NDJSON agent transcript (also the cost source) |
| `final.png` | `bench.py` | the screen at the end of the run - the visual proof, and the only thing the judge scores |
| `env.json` | `isolation.py` | isolation audit trail (host preload, the instruction files OpenCode loaded and the host sources kept out, golden, clone, proxies, reset outcomes, teardown reports). Ignored by scoring. |
| `score.json` | `judge.py` | `verdict` (`success` / `partial` / `fail`), `confidence`, `reason`, `judge_model`, plus the judge telemetry: `judge_input` (the `app` / `prompt` / `solved_screen` / `tool_names` / `n_tool_calls` it was shown) and `judge_prompt` (the rendered prompt, verbatim). Score files written before the telemetry landed have neither - readers fall back to `tasks.json` + `meta.json`. |

`results/ledger.json` is **derived from disk** (`ledger.disk_state`) - if it ever disagrees with the files,
the files win. See [`running.md`](running.md#the-ledger).

## Run lifecycle - full per-run isolation

Nothing survives between runs. The cost (~45-90 s per run of seed + clone + boot + teardown) is deliberate:
correctness over speed. `bench.run_one()` executes, in order:

0. **Host preload** - the tool's one-time host setup (`preload.ensure()`), checked every run and built at
   most once per tool version per process. A failure raises `PreloadError` before the unit writes
   anything. See [`harness.md`](harness.md#host-preload-one-time-tool-setup-outside-the-timer). Then the
   **ambient check** (`ambient.check()`): a host instruction or config source no OpenCode switch keeps out
   raises `AmbientError`, likewise before anything is written, and stops the pass. See
   [`harness.md`](harness.md#ambient-instructions-nothing-of-the-hosts-reaches-a-run).
1. **Teardown (pre)** - kill and `pgrep`-verify every bench process: opencode, argent MCP, tool-server,
   agent-device MCP + its XCTest daemon, both effort proxies. A survivor raises `TeardownError` and aborts
   the matrix - a machine that cannot guarantee isolation must not produce results.
2. **Reap stale clones** - leftover `bench-run-*` simulators from a crashed run.
3. **Check golden** - exactly one simulator must carry the golden's name, and it must be `Shutdown`.
4. **Server reset (pre)** - per-app hook. Bluesky `POST :1987/reset`; element/mastodon re-run their `seed.py`.
   Hard-fail by default: a failed reset stops the matrix rather than scoring a dirty surface.
5. **Clone + boot** the golden into a throwaway `bench-run-<ts>-<task>` simulator.
6. **Write the per-run config** - a temp dir with the clone's UDID baked in, plus the tool's staged skills;
   `env.json` `ambient` records the instruction files OpenCode will load from it.
7. **Launch the app** (`simctl launch`), settle for `LOAD_WAIT`.
8. **Start the effort proxies** - reasoning effort is fixed at spawn via `BENCH_EFFORT_JSON`.
9. **Run OpenCode.** Up to 3 attempts if the agent made **0 tool calls** and did not time out (an
   MCP-not-ready flake, not a model failure).
10. **Screenshot → `final.png`**, write `meta.json`, mark the ledger.
11. **Finally:** server reset (post) → teardown (post) → **destroy the clone** → delete the config dir →
    write `env.json`.

Invariants the harness enforces rather than assumes:

- **One device at a time.** A host-wide `flock` (`/tmp/app-control-bench.device.lock`) makes a second stream
  fail loudly instead of interleaving taps on a shared simulator. `--wait-lock` queues instead.
- **The golden is never mutated.** It stays `Shutdown` forever; runs only ever touch clones. The one
  sanctioned exception is `sim_device.refresh_golden_sessions()` (Bluesky token refresh).
- **Byte-identical start state.** Every run begins from the same golden image, so a task cannot be made
  easier by what the previous run left behind.

## Version stamping and pollution

Every `meta.json` carries a `versions` block:

```json
"versions": {
  "schema": 2, "edition": "2026-11", "harness": "isolation-v5", "surface": "mcp", "skills": true,
  "opencode": "2.3.0", "opencode_pinned": "2.3.0", "tool_loading": "code-mode",
  "tool": "0.15.0", "tool_pinned": "0.15.0", "app": "1.122.0", "app_pinned": "1.122.0",
  "preload": {"state": "present", "s": 0.0}
}
```

`preload` is the run's host preload outcome (`built` / `present` / `none`); runs written before it existed
have no such key. `opencode` / `opencode_pinned` are the installed and pinned OpenCode
(`configs/opencode-version.json`) and `tool_loading` says how MCP tools reached the model
([harness.md](harness.md#tool-loading-code-mode)); runs before `isolation-v5` have none of the three. Nothing gates on it: the ledger, judge, exporter and dashboard read `schema`, `harness`,
`tool` and `tool_pinned`.

`bench.HARNESS` is the current stamp, and a harness change starts a new **edition**
([`editions.md`](editions.md)): the new harness's runs go to a fresh results directory, and the runs of
earlier harnesses stay in theirs, comparable among themselves and shown on the site as their own edition.
Nothing is re-run in place and no two generations share a table.

*Polluted* is what remains of the older mechanism, inside one edition: a run that does not carry its own
edition's harness. The exporter excludes it from every aggregate and renders it as a `stale` chip,
`judge.py` skips it, and `ledger.needs_run()` schedules it to be re-run. In practice that is the runs from
before harness stamps existed, which form an unranked edition of their own. The history of the generations
is in [`history.md`](history.md).
