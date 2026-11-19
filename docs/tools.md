# The tools under test

Four cells (`bench.TOOLS = ["argent", "agent-device", "mobile-mcp", "none"]`), three surfaces
(`bench.SURFACE = {"argent": "mcp", "mobile-mcp": "mcp", "agent-device": "cli", "none": "shell"}`). Each
gets its own `configs/<tool>/opencode.json` template, and only that tool's server is exposed to the agent.

## argent - MCP

- Driven as an **MCP stdio server** (`argent mcp`), the way it ships.
- Its tools are loaded **on demand**: OpenCode's code mode exposes one `execute` tool with a searchable
  catalog instead of putting all 70+ tool schemas in every request. See
  [harness.md](harness.md#tool-loading-code-mode).
- On iOS it is **runnerless**: it drives the device over `simctl` + the accessibility tree
  (`describe` / `tap` / `screenshot`), with no baked-in device runner to install. It auto-targets the single
  booted clone, so no UDID threading is needed.
- All of its shipped skills are staged for progressive disclosure, pinned to the running argent version.
  Its `rules/argent.md` is the one always-on instruction.
- Its native binaries get their **first launch on the host before the timer** (`simulator-server`, and
  `ax-service` up to dyld's refusal to run it outside a simulator). Its tool-server and per-device
  `simulator-server` start inside every run. See [harness.md](harness.md#host-preload-one-time-tool-setup-outside-the-timer).

## agent-device - CLI

- Driven as a **CLI run as shell commands**, which is how the tool is really used.
  `gen_configs` deliberately **pops** the `agent-device` MCP entry from the config.
- Wired via `AGENT_DEVICE_CONFIG=<cfg>/agent-device-cli.json` (`{udid, platform, iosSimulatorDeviceSet}`)
  plus a PATH shim at `~/.cache/bench-shims/agent-device`.
- Its XCTest runner is **built on the host before the timer** (the one-time `xcodebuild build-for-testing`
  into `~/.agent-device/apple-runner/derived/`); its daemon and the runner's launch on the device start
  inside every run. See [harness.md](harness.md#host-preload-one-time-tool-setup-outside-the-timer).
- Quirks that shaped the harness:
  - It must **`open <app>` to bind a session** before `snapshot` will work.
  - A snapshot drives an XCTest runner, which is launched on the run's device inside the timed window.
  - Text-only models must use `snapshot` (the AX tree), **not** `screenshot` (an image they cannot read).
  - Foreground apps via **bundle launch**, not URL-scheme `openurl` - the latter triggers iOS's
    "Open in <App>?" universal-link dialog, which silently contaminated an entire overnight batch. See
    [`history.md`](history.md).

## mobile-mcp - MCP

[Mobile MCP](https://github.com/mobile-next/mobile-mcp) (`@mobilenext/mobile-mcp`), added in the 2026-11
edition as the most widely used tool of the same kind after the two above: 10.6k GitHub stars and about 51k
npm downloads a week on 2026-11-17, against 2.2k and 14k for `ios-simulator-mcp`, the next tool built for the
same job. XcodeBuildMCP has more downloads (72k) but is a build tool first, and Maestro more stars (16.4k) but
is an E2E test framework.

- Driven as an **MCP stdio server** (`mcp-server-mobile`), the way it ships, with its tools loaded on demand
  like argent's ([harness.md](harness.md#tool-loading-code-mode)). 35 tools, all named `mobile_*`.
- It ships **no skills and no rule**, so none are staged: the tool descriptions and the server's own MCP
  instructions are all the agent gets. The preamble tells the agent the simulator's udid is the device id,
  because every tool takes one and there is no default device.
- It does its device work by running **`mobilecli`**, a native binary installed beside it. mobilecli keeps a
  daemon, a socket and its cloud login under `MOBILECLI_HOME`; each run gets a directory of its own under
  `/tmp` (short, because the socket path is limited to 103 bytes), removed with the run. Teardown kills the
  daemon, which reparents itself to launchd.
- mobilecli's **first launch happens on the host before the timer**
  ([harness.md](harness.md#host-preload-one-time-tool-setup-outside-the-timer)).
- On an iOS simulator it needs an **agent app** (`devicekit-ios`) on the device. The MCP server installs it
  the first time a tool targets a simulator that lacks it, which downloads it from GitHub and takes about
  5 s. The golden simulator should carry it, as it carries agent-device's runner
  ([golden-simulator.md](surfaces/golden-simulator.md)); otherwise every run pays that and depends on GitHub
  being reachable.
- **Telemetry is off**: the run config sets `MOBILEMCP_DISABLE_TELEMETRY=1` on the server, which otherwise
  reports usage to PostHog and Scarf on every run.
- It also exposes tools that rent **cloud devices** (`mobile_allocate_remote_device`, ...). They need a
  Mobile Next login, so keep the bench host logged out of it (`mobilecli auth`): a logged-in host would let
  a run reserve a paid device.

## none - the no-tool control

The point of the benchmark is not just to rank two tools against each other but to say **how much they add
over nothing**. The `none` cell is that "nothing".

- `configs/none/opencode.json` is bare: proxies only, **no MCP, no CLI, no skills staged**.
  (It is in fact byte-identical to the agent-device config - the difference between those two cells lives
  entirely in the harness and the preamble, not in the config file.)
- The agent gets only OpenCode's stock shell plus the machine's Xcode command-line tools.
- It **perceives** by shelling out to `xcrun simctl io <udid> screenshot` and then `read`ing the PNG -
  OpenCode's `read` tool does surface images to vision models (confirmed end-to-end).
- It **acts** through `xcrun simctl`, which can `launch` / `openurl` / `terminate` but **cannot tap or
  type**.
- **Host GUI automation is blocked** (`osascript`, System Events, CGEvent, `cliclick`). Left unblocked, the
  bare agent drove the *host* mouse and keyboard to fake taps - a side effect outside the device under test,
  and too flaky to run unattended. The preamble neutrally enumerates what native tooling *is* available
  without prescribing any of it. See [`harness.md`](harness.md#the-none-preamble-neutral-not-bare).
- **`simctl` is scoped to the run's own clone** (`bench_env.none_shim_dir(udid)`). simctl targets *any*
  device on the host and `simctl list` shows the agent the golden by name - and agents do go looking: every
  one of the 57 `none` transcripts referenced the golden, and two of them ran `simctl boot` against it. That
  is not a harmless trespass: launching the app on the golden rotates its frozen Bluesky session, after which
  **every later clone boots logged out** and the rest of the matrix silently scores zero (it happened on
  2026-07-13 - see [golden-simulator.md](surfaces/golden-simulator.md#what-actually-blew-it-up-2026-07-13)).
  The shim rejects any simctl argument naming another device, plus the device-lifecycle subcommands
  (`create`/`clone`/`delete`/`erase`/...). The preamble advertises none of those, so **the baseline's
  advertised surface is unchanged** - only the escape from its own device is closed.

So the baseline can only really do launch/deep-link navigation; tap-and-type tasks fail. **Low success is
the expected, correct result** - that gap *is* the measurement.

## Version pins

`configs/tool-versions.json` records the version each toolkit's **next** runs should be produced with:

| Tool | Pinned |
| --- | --- |
| argent | **0.34.0** |
| agent-device | **0.21.40** |
| mobile-mcp | **1.0.18** (with mobilecli 1.0.24) |

These are the versions the 2026-11 [edition](editions.md) records. The 2026-08 edition ran argent 0.13.0,
0.15.0 and 0.21.0, and agent-device 0.17.6 and 0.20.10.

Installing them on the bench host:

- **argent** - `npm install -g @swmansion/argent@0.34.0`. Its skills and rule ship in the package.
- **mobile-mcp** - `npm install -g @mobilenext/mobile-mcp@1.0.18`, which also installs mobilecli. Then add
  its agent to the golden: `mobilecli agent install --device <golden udid>` while the golden is booted for
  maintenance.
- **agent-device** - `npm install agent-device@0.21.40`, plus the `skills/` directory of
  `callstack/agent-device` at tag `v0.21.40` copied into the installed package, with `BENCH_AGENT_DEVICE`
  pointing at its `bin/agent-device.mjs`. The npm package has the CLI but not the `skills/` the harness
  stages, so without the copy it runs with no agent-device skills. A built checkout of the tag does not work
  instead: its `package.json` says `0.21.40-dev`, which names the result cell and which the host preload,
  verified for exactly 0.21.40, refuses
  ([harness.md](harness.md#host-preload-one-time-tool-setup-outside-the-timer)).

A toolkit's version is part of the axis the site compares, not a footnote on it: a result belongs to
`argent@0.15.0`, and a later argent is another column beside it rather than a replacement for it. The site
shows the newest version of each toolkit and keeps the rest one control away. The on-disk rule that keeps
two versions from landing in the same directory is [`tool-versions.md`](tool-versions.md).

`configs/app-versions.json` pins the target apps - the build of each surface the agents actually drove:

| App | Pinned | Source |
| --- | --- | --- |
| Bluesky | **1.122.0** @ `7f5dd40` | `bluesky-social/social-app` ("Release prep 1.122.0") |
| Element | **1.11.40** @ `36a1788` | `element-hq/element-ios` (`develop`, 2026-06-23) |
| Immich | **3.0.0+3053** @ `237734b` | `immich-app/immich` (mobile app; the server stack is pinned separately at v3.0.1 - see [immich-frozen.md](surfaces/immich-frozen.md)) |

Mastodon/IceCubes is deliberately absent: there is no IceCubes source checkout on the bench host, so no
version is verifiable there. The report shows only the apps it covers.

`bench.py` compares the installed versions against these at the start of every run and **warns on drift**
(non-fatal); the report displays them on the site. Bump the files when a tool is upgraded - a bumped pin
sends the new runs to their own column rather than over the old one, so the pin is a claim about what runs
next, and the runs themselves are the claim about the data.

A tool entry may also be an object, `{"pinned": "0.19.0", "default": "0.17.6"}`, where `default` names the
version the report leads with - for the stretch where a bumped version is only part-way recorded and should
not yet head the page. Everything else reads `pinned`.

Every `meta.json` carries both `tool` (what actually ran) and `tool_pinned`, so drift is auditable after the
fact rather than trusted at run time.

> **argent 0.14.0 is broken on macOS 27** and exposes degraded tooling. 0.15.0 is the first release that
> works there. Treat any pre-0.15.0 argent run as harness-validation data, not a benchmark score.
