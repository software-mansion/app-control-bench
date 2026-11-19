# History - what was tried, what broke, what we threw away

This page exists so nobody re-litigates a settled decision or trusts a dataset that was invalidated. Most
entries cost real time or real API money to learn.

## The generations

### v1 - web apps in Mobile Safari (**dead**)

The original plan drove Bluesky/Expensify/Mastodon **web UIs in Mobile Safari**, because argent supported
Chromium and agent-device did not, making the iOS Simulator the only common surface - and web-in-Safari
avoided heavy native builds.

**Why it died:** a browser is not an app. It measured web-accessibility-tree quality, not device control.
Everything is native now. (`results-safari-invalid/` is in `.gitignore` as a fossil of this.)

Worth keeping from v1: both tools *could* read the web AX tree in Safari. argent gave a fast flat tree;
agent-device gave richer `@e-ref` structure but took ~27 s per snapshot. The blocker was conceptual, not
technical.

### v2 - native apps, per-navigation-category

Native apps on the simulator, `nav_category` on every task, per-task `final.png` kept as visual proof. This
is the shape the benchmark still has.

### Full per-run isolation

Driven by an explicit mandate: *"EVERYTHING terminated between runs, ONE device at a time, sacrifice
execution time."* Added `isolation.py` (flock, verified teardown, per-run proxies, reset hooks, `env.json`)
and `sim_device.py` (golden-clone-per-run). Costs ~45-90 s per run. Worth it.

### Harness v1 → v2 → v3 (skills)

The tool profiles had to be **faithful to how the tools are really used**, and two attempts were wrong.
Every run they produced is pollution:

- **`isolation-v1`** - no skills at all; agent-device driven over MCP (it is really a CLI).
- **`isolation-v2-skills`** - force-fed the *full* skill text always-on via `instructions`, which is not how
  a real agent gets skills. Only staged 1 of argent's 13 skills. **And it leaked the operator's ambient
  `~/.claude` / `~/.agents` skills into every run** - so it partly measured the laptop.
- **`isolation-v3-progressive`** - OpenCode's native progressive disclosure, ambient skills
  suppressed, agent-device driven as a CLI and argent as MCP.

See [`harness.md`](harness.md).

### Harness v4 (host preload, no ambient host instructions)

**`isolation-v4`** *(current)* is v3 plus two things:

- **The host preload**: each tool's one-time host work (agent-device's XCTest runner build, argent's first
  binary launches) runs before the timer instead of inside whichever run of that tool goes first on a host,
  whose `wall_s` could otherwise carry that one-time cost. See
  [`harness.md`](harness.md#host-preload-one-time-tool-setup-outside-the-timer).
- **No ambient host instructions** (internal issue #11): v3 kept the host's skills out but not its
  instructions. OpenCode loaded the operator's `~/.claude/CLAUDE.md` into every run's system prompt - on the
  bench host that put its `CLAUDE.md` into published qwen runs - and would equally have loaded a global
  `AGENTS.md`, the global `opencode.json`'s `instructions` / `mcp` / `agent` keys, host agents, modes, skills
  and plugins. v4 disables what OpenCode has a switch for and refuses to run on a host with anything else.
  See [`harness.md`](harness.md#ambient-instructions-nothing-of-the-hosts-reaches-a-run).

v3 runs are superseded and re-run in place like any older harness.

### The `none` baseline (2026-07-10)

Added a third tool cell with **no device-control tool at all** - stock shell plus Xcode CLI. It answers the
question the two-tool comparison cannot: *how much do these tools actually add over nothing?*

Its preamble was initially **bare** (not even a `xcrun simctl` hint), on the reasoning that naming a toolset
would coach the control cell. **That was reverted on 2026-07-13**, because the unframed agent started
**driving the host mouse and keyboard** via `osascript` / System Events / CGEvent to fake taps - a side
effect outside the device under test, and far too flaky to run unattended. The baseline now blocks host GUI
automation on PATH and gives a strictly *neutral enumeration* of the native tooling that exists, never an
imperative to use it. ([details](harness.md#the-none-preamble-neutral-not-bare))

## Data that was thrown away, and why

- **`gpt_low:argent` `element-03` and `element-30` — FLEX debug overlay (2026-07-16).** The Element **Debug**
  build linked `FLEX 5.22.10`, and `AppCoordinator.setupFlexDebuggerOnWindow` wired a DEBUG-only **two-finger
  double-tap** window gesture to `FLEXManager.shared.showExplorer()`. argent's raw HID touch injection tripped
  it; agent-device's accessibility path never did (0/60 agent-device vs ~10 % of argent Element runs). Once
  open, the full-screen FLEX inspector ate taps/keyboard and the agent could not finish. **Both runs' `final.png`
  shows FLEX itself** — `element-03` = FLEX Network History ("0 Requests (Zero KB received)"), `element-30` =
  the FLEX UIApplication object explorer (`keyWindow <FLEXWindow…>`) — preserved at commit `72aefd6`. Higher-effort
  cells hit the same overlay but dug out of it, so the pollution surfaced as a **`gpt_low`-specific anomaly** (69 %
  vs ~90 % for its peers). **Fix:** dropped the `FLEX` pod and guarded the three `AppCoordinator` refs with
  `#if canImport(FLEX)`, rebuilt a FLEX-free `Element.app`, re-goldened, and regenerated the cell. Only these two
  ended *trapped* in FLEX; FLEX explains ~2 of the 12 `gpt_low:argent` failures — the rest is genuine low-effort
  weakness on argent's coordinate surface. ([details](surfaces/element.md#️-pollution-hazard-flex-debug-overlay-debug-build-only))
- **The 64 polluted Element runs.** The iOS *"Open in 'Element Classic'?"* dialog contaminated ~half an
  overnight batch. It appeared in `final.png` even when the agent never snapshotted it, so transcript-grep
  found only 6 of 64 - a **vision pass over every screenshot** found the rest. Purged.
  ([details](surfaces/element.md#history-the-tooling-blocker-that-killed-a-whole-batch))
- **Every `score.json` in the repo** (509 local + 124 on the bench host). The `gpt-4.1-mini` vision judge
  could not reliably OCR screenshots and produced both false positives and false negatives. All runs were
  reset to judgement-pending rather than ship scores nobody trusted. Re-judged with GPT-5.4.
  ([details](judging.md#judge-history-why-it-is-gpt-54))
- **All pre-0.15.0 argent runs.** argent 0.14.0 is broken on macOS 27 and exposes degraded tooling - the
  model fights the tool rather than the task. Those runs are harness-validation data, not scores.
- **Old real-`bsky.social` screenshots.** Before the self-hosted stack, runs hit the real Bluesky. Those
  screenshots show a real Discover feed and the `tester-guy.bsky.social` handle. Do not ground new
  `solved_screen` descriptions on them - use current self-hosted screenshots only.
- **`silver-v10rsn2`'s 0/30 run** - invalid, an artifact of the 0-tool-calls bug, not a model result.

## Decisions that are settled

- **Native, not web.** (v1)
- **One device at a time**, enforced by a host-wide lock. A second stream fails loudly rather than
  interleaving taps.
- **Clone-per-run**, not reset-in-place. Byte-identical start state beats a cleanup script you have to trust.
- **The golden is never booted** except for the sanctioned Bluesky session refresh.
- **Reset hooks hard-fail.** Stopping the matrix beats scoring a dirty surface.
- **Judge = GPT-5.4 vision @ temp 0**, with a per-task `solved_screen`.
- **The label must equal the model that actually ran.** (models.md)
- **Element `11`/`12`/`13` were rewritten** - the originals targeted Element X features absent from Element
  Classic. "Favourite a room" was rejected as a task: Element Classic shows no visible favourite indicator,
  so there is nothing for the judge to see.
- **Foreground apps via bundle launch, never URL-scheme `openurl`** - the latter triggers the universal-link
  dialog that poisoned the Element batch.

## Branches

| Branch | Purpose | State |
| --- | --- | --- |
| **`main`** | SoTA cloud models. The live branch. | active |
| **`silver`** | `gemma` + internal `silver` fine-tune variants. | active, paused |
| **`stylized`** | 1-bit phosphor-CRT reskin of the report. | active ([rules](reporting.md#the-stylized-branch)) |
| `harness/autonomy`, `isolation/full-reset`, `faithful/tool-skills`, `judge-labels`, `dashboard`, `bench/main-sota-run` | Feature branches, all long since merged or superseded by direct commits to `main`. | **stale - safe to delete** |

The early work went through draft PRs (#1-#5). The user then said *"commit directly, this repo is all
yours"*, so later work landed straight on `main` / `silver`. The old PRs and their branches are superseded.

## Infrastructure history

- **The report's hosting left this repo and came back.** It ran for a while from a separate public GitLab
  repo seeded by a manual copy of this tree, which meant UI work landed there first and had to be ported
  back across two different layouts. The pipeline now lives here and runs two sites - public from a tag,
  internal from every push to `main` - and what either is allowed to contain is a config file rather than
  a copy discipline. (details)
- **Immich's iOS build blocked the surface for weeks** on an Xcode-26.5 Swift-macro failure. Resolved by
  pinning to **Xcode 26.2**. ([details](surfaces/immich.md#building-the-ios-app-three-hard-won-fixes))
- **A radoniusz reboot once deleted the Element simulator outright**, taking the logged-in session and the
  seeded rooms with it. The built `Element.app` artifact survived on disk, so recovery was
  `simctl create` + `simctl install` + re-login, not a rebuild. Synapse's DB persisted everything.
- **The 2026-07-17 dev-env restart logged the Bluesky golden out.** Bringing radoniusz's services back up
  (Colima + dev-env) recreated the atproto containers, which rotated every account DID - the exact
  "do not blind-restart" hazard in [bluesky.md](surfaces/bluesky.md#the-backend---self-hosted-atproto). The
  golden's frozen session referenced the dead DID, so a cold clone booted to the signed-out landing screen;
  the one missing SoTA unit (`gpt_low:argent/bsky-08`, a phantom `doctor --heal` had just reset) would have
  scored a guaranteed zero. Recovered without a full re-golden: `bsky_login.py` on radoniusz (mirrors
  `element_login.py` - a model + argent drives the sign-in as `bench.test` → `http://localhost:3000`), then
  a cold-clone `--verify-golden` before the run. Gotcha: the generated run config routes providers through
  the per-run effort proxies (`:8788`/`:8790`), which are **down at rest** - a one-off login must strip the
  `provider` override or opencode hangs silently, producing zero output and zero taps.
- **Don't spawn recursive sub-agent swarms.** A research sub-agent once recursively spawned ~20 of its own
  and burned a weekly token budget down to 2%.
