# The harness - how the agent is configured

The benchmark is only worth reading if each cell reproduces how the tool is *actually used in the real
world*. Two earlier harness generations got this wrong and every run they produced is pollution. This page
records the rules and why they exist.

## Integrity rules

- **Full, untampered OpenCode harness.** Never shrink the system prompt or the tool catalog to help a tool.
- **Same preamble, same judge, same task set** for every cell.
- **Same surface.** argent, agent-device, mobile-mcp and `none` all drive the *same native app on the same simulator
  image*. A common native surface is the entire point.
- **Keep every `final.png`.** It is the visual proof for a verdict and the only thing the judge sees.

## Host preload: one-time tool setup outside the timer

A tool can carry a one-time cost on the host - a runner it compiles, a cache it fills, a download, a
binary's first launch. Left alone, that cost lands inside whichever timed run of the tool happens to go
first on a host. The rule is the benchmark's, not any tool's:

> **One-time work on the HOST that touches no device, app or session may happen before the timer.
> Anything that touches the run's device, app or session stays inside the timed window.**

Each tool declares its preload in **one table**, `runner/preload.py` `PRELOAD`, and every entry goes through
the same `preload.ensure()`:

- An entry is a list of **steps** (argv + timeout) and a **check**: a command that exits 0 only when the
  artifact the steps produce is in place. An entry with steps and no check is refused, so a preload can
  never pass without its artifact.
- A tool with nothing to preload has an **explicit empty entry** whose `why` says how that was established.
  A tool with no entry at all is an error.
- `bench.run_one()` calls `ensure()` for every run, before any device work, so `run_all.sh`,
  `run_matrix.py` and a direct `bench.py` all get it. The check runs every time; the steps run at most once
  per tool version per process, and only when the check fails.
- **Failure is fatal for that tool.** A failing step, a check that still fails after the steps, or an
  artifact that vanishes after being built raises `PreloadError` with the output tail. It is raised before
  the unit writes anything, so the unit stays **pending**; the matrix skips the rest of that tool's units
  in the cell or queue entry and carries on with the other tools. A later call for the same version fails
  at once, without retrying.
- **Audit.** `meta.json` `versions.preload` is `{"state": "built" | "present" | "none", "s": <seconds>}`;
  `env.json` `preload` adds the check and each step's argv, rc and duration. A run without the key predates
  the preload, and the first run of a tool on its host may carry that tool's one-time cost.

| Tool | Preload | Why |
| --- | --- | --- |
| `agent-device` | Its XCTest runner, built into `~/.agent-device/apple-runner/derived/` | The first session on a host runs `xcodebuild build-for-testing`, keyed on the runner source, its build settings and the Xcode/SDK versions, never on a device. |
| `argent` | The first launch of its native binaries: `simulator-server fingerprint` on the host, and `ax-service`, which dyld refuses to start outside a simulator once macOS's launch checks have run | macOS runs one-time checks on a newly installed binary's first launch (0.15.0: ~0.2 s and ~0.1 s). That state lives in the OS, so the check reads a record `runner/preload-argent.py` writes under `~/.cache/bench-preload/`, keyed on the binaries' identity and the boot. |
| `mobile-mcp` | The first launch of `mobilecli`, with `--version` | The same one-time launch checks as argent's binaries, on the native binary Mobile MCP runs for every device action. The check reads a record `runner/preload-mobile-mcp.py` writes under `~/.cache/bench-preload/`. The agent app it installs on a simulator is per device, so it is not preloaded. |
| `none` | nothing | No tool. |

Measured on a dev host, same simulator, argent's launchd environment cleared before each timed session as
on a fresh clone:

| First timed commands | Cold host | Preloaded | Warm host |
| --- | --- | --- | --- |
| agent-device 0.17.6 `open` + `snapshot` (median of 3) | 13.3 s | 4.4 s | - |
| argent 0.15.0 over MCP, `describe` + `screenshot` (median of 5) | 5.27 s | 5.19 s | 4.89 s |
| argent, new install on a used HOME (a version bump) | 5.28 s | 5.06 s | 4.89 s |

Those were measured at the versions named. At the versions now pinned (agent-device 0.21.40, argent 0.34.0)
the preloads themselves were re-run on the bench host - agent-device's runner build took 10.3 s, argent's
two launches 0.8 s, mobilecli's 0.1 s - but the timed-session comparison was not repeated.

agent-device's preload takes ~10 s outside the timer, argent's under a second. What argent's preload leaves is
per-install work inside the simulator: `ax-service` and the dylibs loading there. `~/.argent` makes no measurable
difference over MCP, and the preload does not touch it, because its `update-suppression.json` decides
whether the model sees argent's update notice: only a tool result the tool-server hands back writes it.

**What stays timed.** Per-run MCP / daemon / tool-server startup, and everything on the device: for
agent-device, installing and launching the runner on the clone (`xcodebuild test-without-building`); for
argent, the tool-server (which reconfigures every booted simulator as it starts), the per-device
`simulator-server`, `ax-service` and the injected dylibs.

**agent-device 0.21.40 has no device-free build command** (`prepare ios-runner` builds the runner and then
starts it on a device). `runner/preload-agent-device.mjs` therefore calls the function every session calls,
`ensureXctestrunArtifact`, so the artifact and its manifest are exactly what the tool's own next session
looks for. Its check asks the tool's own `evaluateExistingXctestrun` whether a cache directory is reusable
for the metadata the tool expects now, so it is the tool's reuse test, not a copy of it. The tool formats
the build's `-destination` as `platform=iOS Simulator,id=<udid>`, which xcodebuild does not match to its
generic placeholder, so the build names a real simulator: `sim_device.build_destination()`
(`BENCH_PRELOAD_SIM`, else the first available iOS simulator by udid), never the golden or a `bench-*`
device. xcodebuild only resolves it, and the cache key has no device in it.

None of that is public API, and how the script reaches it is tied to the pinned version:

- The bundle is minified, and its functions are exported under one-letter names that change with every
  build. The script finds each function by strings its source must contain, in a named module, and refuses
  a build where one is missing or two match.
- The runner code reaches the host through two bindings the tool's daemon makes at start-up. The script
  makes the same two: it loads the tool's composition root (`dist/src/runner-client.js`) and calls
  `setAppleRunnerHostEnvironment` with the process environment.
- It refuses any version but the one it was verified for, so a version bump fails loudly until the entry is
  re-derived. `test_preload.py` fails if that version and the pin in `configs/tool-versions.json` differ.

Earlier versions differed: through 0.17.6 the cache lived in `~/.agent-device/ios-runner/` and the function
had to be exported by a load hook on a hashed chunk; 0.18.3 added `doctor --platform ios`, which starts the
same build in the background without waiting for it; 0.19.0 moved the cache to
`~/.agent-device/apple-runner/`.

A tool added to `bench.TOOLS` needs a `PRELOAD` entry - empty, with its evidence, if it has nothing to
preload.

## Skills: progressive disclosure (`isolation-v3-progressive`)

In the real world a coding agent does **not** get a tool's documentation pasted into its system prompt. It
gets a `skill` tool plus an `<available_skills>` name+description index, and loads a `SKILL.md` body **on
demand**. `skill` is a *harness* capability, not a per-tool one - so agent-device participates in it too,
even though it is driven as a CLI.

`gen_configs._stage_skills()` reproduces exactly that:

- Each tool's **real shipped skills** are copied into the per-run
  `<cfg>/.opencode/skills/<name>/SKILL.md` - OpenCode's native progressive-disclosure surface.
  - **argent** → all skills from `<argent-pkg>/skills`, pinned to the *same argent version* whose
    `argent mcp` the run drives (`bench_env.argent_pkg_dir()`).
  - **agent-device** → its skills from `<agent-device-repo>/skills`. Its `SKILL.md` routes to on-demand
    `agent-device help <topic>` calls in the shell - itself faithful progressive disclosure.
  - **none** → nothing. No skills, no MCP, no CLI target.
- Only a tool's `alwaysApply: true` **rule** goes into always-on `instructions` (argent ships
  `rules/argent.md`; frontmatter is stripped). agent-device ships no rule.

### Ambient suppression (critical)

Without it, the *host machine's* `~/.claude` and `~/.agents` skills (pptx, runpodctl, personal skills…) leak
into the agent's context and the benchmark measures the operator's laptop instead of the tool. OpenCode 2
loads both through one built-in plugin, so every config template removes it:

```json
"plugins": ["-*", "opencode.*", "-opencode.config.compatibility"]
```

`-*` removes every plugin, `opencode.*` puts OpenCode's own back, and the last entry drops the loader for
`.claude/skills` and `.agents/skills`, in the home dir and in every directory above the run dir.
`ambient.RUN_ENV` also sets `OPENCODE_DISABLE_CLAUDE_CODE=1` and `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`,
which cover the `.claude` half on their own. (OpenCode 1's `OPENCODE_DISABLE_EXTERNAL_SKILLS` no longer
exists.)

The agent's `<available_skills>` index then holds the staged skills plus OpenCode 2's two built-in ones
(`opencode`, `report`), which are part of the stock agent and the same in every cell.

Skills are only one channel. The host's instructions and config are the rest:
[ambient instructions](#ambient-instructions-nothing-of-the-hosts-reaches-a-run).

## Ambient instructions: nothing of the host's reaches a run

OpenCode builds every run's system prompt and config from the host as well as from the run dir: the
operator's `~/.config/opencode/AGENTS.md`, the global `opencode.json` (its `instructions`, `mcp`, `agents`,
...), agents, modes, skills and plugins under `~/.config/opencode/`, and an `opencode.json` or `.opencode/`
in any directory above the run dir (a temp dir, so its ancestors reach `/`). Older harness generations keep
none of it out: a bench host's `~/.claude/CLAUDE.md` is in their published runs' system prompt (internal
issue #11).

`runner/ambient.py` lists every source OpenCode 2.3.0 reads, from its source, and handles each one. The
list was checked against the 2.3.0 binary: on a host with a marker file in every location, the markers that
reach the model are exactly the sources `scan()` refuses.

| Handling | Sources |
| --- | --- |
| **Neutralised** by `ambient.RUN_ENV` | `.claude/skills` in the home dir and above the run dir (`OPENCODE_DISABLE_CLAUDE_CODE=1`, `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`); `OPENCODE_CONFIG`, `OPENCODE_CONFIG_CONTENT`, `OPENCODE_CONFIG_DIR`, `OPENCODE_FLAGS` and the other hooks a launcher may export (blanked) |
| **Neutralised** by the run's own config | every host plugin, from a config's `plugins` or a `plugin(s)/` dir, and `.agents/skills` in the home dir and above the run dir (the `plugins` list above); a global MCP server the template disables (`radon`, `linear`); host permission rules (the template's `permission: "allow"` is matched last); a last-document-wins key the template sets itself (`websearch`) |
| **Refused** | `~/.config/opencode/AGENTS.md`; an `AGENTS.md` between the run dir and its project root; `agent(s)/`, `mode(s)/`, `skill(s)/` under `~/.config/opencode/`, `/Library/Application Support/opencode/` or a `.opencode/` above the run dir; any key outside `ambient.ALLOWED_KEYS` in a global, ancestor or managed config; remote config (a well-known source or an OpenCode Console credential in OpenCode's database, a `wellknown` entry in a 1.x `auth.json`, `$OPENCODE_API_KEY`); an empty `$OPENCODE_TEST_HOME` |
| **Let through** | provider plumbing (`provider(s)`, `model`, `small_model`, `enabled_providers`, ...), inert keys (logging, sharing, TUI, ...) and slash commands, none of which reaches the model |

What changed from OpenCode 1:

- `OPENCODE_PURE`, `OPENCODE_DISABLE_EXTERNAL_SKILLS` and `OPENCODE_DISABLE_CLAUDE_CODE_PROMPT` are gone. The
  first two are replaced by the `plugins` list in the templates; the third is not needed.
- `CLAUDE.md` and `CONTEXT.md` are not read anywhere, `~/.opencode` is not a config dir, and an `AGENTS.md`
  above the run dir is read only when an ancestor holds `.git` / `.hg` (the run dir is otherwise its own
  project root) or the run dir is under the home dir.
- A template can no longer disable a host MCP server with `{"enabled": false}` alone: OpenCode 2 ignores an
  entry without a `type`, so the entry needs `"type": "local", "command": ["true"]` as well.
- `/Library/Application Support/opencode/` is a whole config directory, not just a config file.
- The templates set `"websearch": false`. With web search available, OpenCode 2 asks which provider to use
  the first time the model searches, and a headless run cannot answer: the run ends with exit code 1.

The harness never rewrites host config (the global `opencode.json` also holds the provider block that routes
the ollama cells). For a source with no OpenCode switch, `ambient.check()` runs in `run_one`
right after the preload, before the unit writes anything, and raises `AmbientError` naming each file and
what to do about it. The unit stays **pending** and the pass stops, since every later run would hit the same
host. `python3 runner/doctor.py --preflight` runs the same check under `== AMBIENT ==`.

**Audit.** `env.json` `ambient` is `ambient.record()` of the run dir: `instructions` lists every instruction
file OpenCode loads into the system prompt (for argent exactly its staged `always-on-rule.md`, for the others
none), `neutralised` the host sources present and kept out, `ambient` what would have reached the run (always
empty, since `check()` passed).

## OpenCode 2

The harness runs on OpenCode 2 (`configs/opencode-version.json` pins 2.3.0). `isolation-v1` to `-v4` ran on
1.x. `bench.py` prints pinned vs installed at start-up, stamps the installed version into every run, and
`require_opencode()` raises `OpencodeVersionError` in `run_one` on a 1.x build, before the unit writes
anything, so the unit stays **pending** and the pass stops.

Each run is `opencode run --standalone --auto --format json --model <id> <prompt>`:

- `--standalone` gives the run a private server, a child of the run's process group. Without it OpenCode 2
  hands the run to a shared background service, which would execute the agent in the service's environment
  (not the run's `PATH` shim, `AGENT_DEVICE_CONFIG` or `ambient.RUN_ENV`) and outlive the run.
- `--auto` approves whatever the run config's `permission: "allow"` does not already cover. A headless run
  cancels the `question` tool by itself, so it cannot block.
- The transcript is the same JSONL as on 1.x (`step_start`, `tool_use`, `text`, `step_finish` with `tokens`
  and `cost`). The shell tool is now called `shell`, not `bash`.

The run config templates stay in the 1.x format, which OpenCode 2 still reads, with three additions that
[keep the host out](#ambient-instructions-nothing-of-the-hosts-reaches-a-run): a `plugins` list, `"websearch":
false`, and a `type` and `command` on the entries that switch off a host's `radon` / `linear` MCP servers.

### Tool loading: code mode

OpenCode 1.x registers every tool of an MCP server as a direct tool, so each request carries every schema.
For argent that is 70+ tools, of which a median run uses 4. In the `isolation-v3-progressive` results
argent's first request is 43k-59k tokens against 7k-10k for agent-device, whose CLI puts no schema in the
prompt, and that larger starting prompt explains 60-79% of argent's cost gap.

OpenCode 2 loads MCP tools on demand instead (**code mode**, its default; Claude Code and Codex load tools
lazily too):

- The MCP tools leave the tool list. The system prompt carries a short catalog (the server's tool count, a
  couple of full signatures) and a `search` function over the rest.
- The model calls a tool from a script passed to one `execute` tool, `await tools.argent.describe({...})`,
  and may make several calls in one script.
- `execute` and the catalog section are present in every cell, with or without an MCP server.

Measured on the same argent run config against a stub model (argent 0.33.0, 71 tools): the first request
is 187.5k characters with every schema preloaded (`codemode: false`) and 41.3k with code mode. The
agent-device cell's is 25.1k.

**Audit.** `meta.json` `versions` records `opencode`, `opencode_pinned` and `tool_loading: "code-mode"`.
`n_tool_calls` / `tool_names` count the MCP calls made inside each `execute` (named `argent.describe`), so
the count stays one of device actions; catalog searches are not counted, and an `execute` that reached no
tool counts once. `mcp_direct_calls` is the number of calls that went to the tool's MCP server as direct
tools (`argent_describe`). It must be 0: anything else means code mode was off, and the run prints
`CODE MODE NOT APPLIED`. A call to a tool that does not exist is not one of them: when a model makes up
a name such as `argent_describe_placeholder`, OpenCode refuses it (`No tool named ... is currently
available`) and nothing reaches a server. Those calls stay in `tool_names` and are counted in
`unknown_tool_calls`.

### Credentials

OpenCode 2 keeps provider keys in its database. It imports a 1.x `auth.json` once, when the database is
created, and never writes the file again. `bench_env.auth_store()` (which cells can run), `doctor` and the
judge therefore ask OpenCode (`opencode auth list` / `auth export`, with `--standalone`) and fall back to
the 1.x file only when that fails.

### Effort proxies

The proxies are unchanged for Anthropic. For OpenAI, OpenCode 2 first tries the Responses API over a
WebSocket, which the proxy could only relay unmodified, so `openai-reasoning-proxy.js` refuses the upgrade
and OpenCode falls back to HTTP POSTs, each of which gets the cell's effort as before.

## The preamble

`bench.preamble(app, udid, tool)` builds an autonomous, action-forcing prompt (an early version let a weak
model *ask the user which app to open* instead of acting). The only part that varies by tool is the `how`
clause:

| Tool | `how` clause |
| --- | --- |
| `argent` | "Drive it with the argent MCP tools. Consult your available argent skills…" |
| `agent-device` | "Drive it with the agent-device CLI, which you run as shell commands…" |
| `none` | A strictly **neutral enumeration** of the native tooling that exists on the system - see below. |

### The `none` preamble: neutral, not bare

The `none` clause was originally **empty**, on the reasoning that naming `xcrun simctl` would coach the
control cell into a strategy the tool cells didn't get.

That backfired. Left with no framing at all, the bare agent **bootstrapped taps by driving the host mouse and
keyboard** (`osascript` → System Events / CGEvent). That is a side effect *outside the device under test*,
and it is far too flaky to run unattended for a full pass. So the baseline is now confined to device-scoped
tooling from both ends:

- **Host GUI automation is blocked.** `bench_env.none_shim_dir()` puts PATH shims in front of `osascript`,
  `osacompile`, `automator`, and `cliclick` that fail fast (exit 127); the Python Quartz/CGEvent path is
  separately closed (no pyobjc). Wired in via `run_env["PATH"]` for `tool == "none"`.
- **The preamble neutrally lists what exists** - a POSIX shell, the Xcode command-line tools
  (`xcrun` / `xcodebuild` / `xctest`), the `xcrun simctl` subcommands (`io … screenshot`, `launch`,
  `terminate`, `openurl`, `listapps`, `spawn`, `ui`, …), and the fact that `read` renders images - plus a
  factual note that host GUI automation is unavailable.

The distinction that keeps this fair: it **lists availability, it never prescribes** ("you must / you may
use X"). An imperative would bias the baseline toward a specific toolset and make it an
xctest/simctl baseline rather than a `none` baseline. Stating what is on the machine is not a strategy;
telling the agent which of it to use would be.

## Reasoning effort: the proxies

Effort is not a model-roster axis; it is injected by a local HTTP proxy per provider:

| Provider | Proxy | Port |
| --- | --- | --- |
| anthropic | `runner/anthropic-thinking-proxy.js` | 8788 |
| openai | `runner/openai-reasoning-proxy.js` | 8790 |
| openrouter | `runner/openrouter-reasoning-proxy.js` | 8792 |

`isolation.start_proxies()` spawns them **fresh per run**, with the effort fixed at spawn time via
`BENCH_EFFORT_JSON` (from `bench.EFFORT[model_key]`). There is no shared mutable control file - an earlier
design used `/tmp/bench-effort.json` and a crashed run could leave the next one at the wrong effort.

Which proxy a run starts comes from `isolation.providers_needed()`, matching the model id's leading
segment against `PROXY_SPECS` - so `openrouter/anthropic/claude-haiku-4.5` starts the OpenRouter proxy
only, never the Anthropic one.

Between runs every port must be **down**; `doctor.py` checks this, and a port still listening after teardown
raises `TeardownError`.

The two native proxies each speak their vendor's own API (Anthropic `thinking` on `/v1/messages`, OpenAI
`reasoning_effort` on `/v1/chat/completions`). OpenRouter needs neither shape: it is one OpenAI-compatible
endpoint whose unified `reasoning:{effort}` it translates per vendor downstream, so its proxy injects one
field for every family and keeps no budget table of its own. The **family keys are shared across all three**
(`gpt54`, `gpt55`, `haiku`, `opus`), which is what lets an `or_*` cell reuse its native twin's `EFFORT`
entry and request the same effort by construction.

Ollama models need no proxy (`providers_needed()` returns `[]`).

## Harness generations

| Stamp | What it did | Status |
| --- | --- | --- |
| `isolation-v1` (or no stamp) | No skills at all; agent-device driven over MCP. | **Pollution** |
| `isolation-v2-skills` | Full skill text force-fed always-on via `instructions` - not how a real agent gets it. Only 1 of argent's skills. Leaked ambient host skills. | **Pollution** |
| `isolation-v3-progressive` | Native progressive disclosure + ambient suppression + agent-device as CLI. | **Superseded** |
| `isolation-v4` | v3 + each tool's one-time host work preloaded before the timer ([host preload](#host-preload-one-time-tool-setup-outside-the-timer)) + no ambient host instructions: the host's `CLAUDE.md`, `AGENTS.md`, global OpenCode config, agents, skills and plugins are kept out of every run or refuse it ([ambient instructions](#ambient-instructions-nothing-of-the-hosts-reaches-a-run)). | **Superseded** |
| `isolation-v5` | v4 on [OpenCode 2](#opencode-2) (pinned, stamped into each run): MCP tools loaded on demand through code mode instead of every tool schema in every request ([tool loading](#tool-loading-code-mode)), a private server per run, and the ambient audit redone for 2.x's host sources. | **Current** |

A harness generation is part of what an [edition](editions.md) freezes: each generation's runs are compared
only with each other, in their own results directory and on their own pages of the site. Runs from before
`isolation-v3-progressive` carry no usable stamp and form an unranked edition. See
[`architecture.md`](architecture.md#version-stamping-and-pollution).
