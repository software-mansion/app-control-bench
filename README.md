# AppControlBench

How well can an LLM agent actually operate a real iOS app - and how much of that ability comes
from the model versus the tool you hand it?

This repo is the benchmark we built to answer that. It runs coding agents against real, unmodified
iOS apps in the Simulator, gives them a plain-language task ("open the Feeds tab", "in the Book Club
room, reply to one of the existing messages with the text 'sounds good'"), and grades the final
screenshot. Every run is isolated, every screenshot and transcript is published, and the whole
matrix is reproducible from this repo.

**Results, run explorer and per-run screenshots: [appcontrolbench.swmansion.com](http://appcontrolbench.swmansion.com/)**

**Disclosure:** [argent](https://github.com/software-mansion/argent) is built by Software Mansion, who
also built this benchmark. That is why the setup is published before the run, and why every run's
screenshot, transcript, prompt, verdict and timing ships in this repo afterwards: so you can check the
result rather than take our word for it.

## November 2026 edition: the setup, published for review before the run

**This branch holds the setup of the next edition. It has not been run yet, and there are no results
for it.** We are publishing the harness, the configs and the tasks first so that the makers of the
tools we compare argent with can tell us what is wrong or unfair about how their tool is set up, before
any number exists.

| | November 2026 | August 2026 (published) |
| --- | --- | --- |
| Agent harness | [OpenCode](https://opencode.ai) 2.3.0, MCP tools loaded on demand ("code mode") | OpenCode 1.4.3, every MCP tool schema in every request |
| Models | `claude-haiku-5-5` and `gpt-6-luna`, each at low and high reasoning effort | haiku-4.5 and gpt-5.4-mini, low and high |
| Tools | argent 0.34.0, agent-device 0.21.40, Mobile MCP 1.0.18, and a no-tool control | argent 0.15.0, agent-device 0.17.6, no-tool control |
| Judge | GPT-6.1 Sol, vision, temperature 0 | GPT-5.4, vision, temperature 0 |
| Tasks | 60: Bluesky 1.122.0 (30) and Element iOS 1.11.40 (30) | the same 60 |
| Matrix | 4 models x 4 tools x 60 tasks = 960 runs, one attempt each | 720 runs |

All three are the releases current on 2026-11-19. If a newer release of your tool should be the one
measured, say so and we will pin that.

### How each tool is set up

Each tool gets its own run config, generated from a template in
[`configs/`](configs/) by
[`gen_configs.py`](runner/gen_configs.py), and only that tool is exposed to the agent. The
agent is told what the task is and which tool to use in a short preamble
([`bench.preamble`](runner/bench.py)); everything about *how* to use the tool comes from
what the tool itself ships.

| | [argent](https://github.com/software-mansion/argent) | [agent-device](https://github.com/callstack/agent-device) | [Mobile MCP](https://github.com/mobile-next/mobile-mcp) | no tool |
| --- | --- | --- | --- | --- |
| Version | 0.34.0 | 0.21.40 | 1.0.18 (mobilecli 1.0.24) | - |
| Surface | MCP stdio server (`argent mcp`) | CLI, run as shell commands | MCP stdio server (`mcp-server-mobile`) | stock shell, Xcode CLI tools, `xcrun simctl` |
| Installed from | npm `@swmansion/argent` | npm `agent-device`, plus the `skills/` directory of the repo at tag `v0.21.40` | npm `@mobilenext/mobile-mcp` | - |
| Guidance staged for the agent | every skill the package ships, and its `rules/argent.md` | every skill in the tag's `skills/` | none: it ships no skills or rules, so the agent gets its tool descriptions and the server's own MCP instructions | none |
| Preamble sentence | "Drive it with the argent MCP tools. Consult your available argent skills for how to interact with the device." | "Drive it with the agent-device CLI, which you run as shell commands. This simulator is already agent-device's default target, so you do not need to pass --udid. Consult your available agent-device skill(s) and the CLI's own help for the workflow." | "Drive it with the mobile-mcp MCP tools, passing this simulator's udid as their device." | a neutral list of the native tooling on the system |
| Device targeting | the one booted clone | `AGENT_DEVICE_CONFIG` names the clone, so no `--udid` is needed | the udid is in the preamble, since every tool takes a device id | `simctl` is scoped to the run's own clone |
| On the golden simulator | nothing | its XCTest runner, installed | its `devicekit-ios` agent app, installed | nothing |
| One-time host work done before the timer | first launch of its native binaries | `xcodebuild build-for-testing` of its XCTest runner | first launch of `mobilecli` | nothing |

What is the same for every cell:

- **The agent.** OpenCode 2.3.0 with the same permissions, no web search, no plugins beyond its own, and
  nothing of the host's own agent configuration: instructions, skills, agents and MCP servers found on
  the host are either switched off by the run's config or stop the run before it starts ([`ambient.py`](runner/ambient.py)).
- **Tool loading.** MCP tools are not put in the prompt as schemas. OpenCode 2 gives the model one
  `execute` tool and a searchable catalog, in every cell, with or without an MCP server. A run where a
  tool was called directly instead is flagged (`mcp_direct_calls` must be 0).
- **Reasoning effort.** Set per request by a local proxy in front of each provider, so the model and
  the effort are exactly what the cell name says.
- **The clock.** It starts when the agent starts and stops when it exits or hits the 15-minute cap.
  One-time work on the host that touches no device, app or session happens before it
  ([`preload.py`](runner/preload.py)); anything that touches the run's device stays inside.
  That rule is the same for every tool, and each tool's preload is declared in one table.
- **The device and the backend.** A fresh clone of one golden simulator per run, and the app's server
  reset before each run.
- **The judge.** One model, one prompt, the final screenshot only.

### Where to look

| If you want to check | Read |
| --- | --- |
| How your tool is wired, installed and versioned | [`docs/tools.md`](docs/tools.md), [`configs/`](configs/) |
| What the agent is told, which skills it gets, code mode, the preload rule | [`docs/harness.md`](docs/harness.md), [`bench.py`](runner/bench.py), [`gen_configs.py`](runner/gen_configs.py), [`preload.py`](runner/preload.py) |
| What is installed on the simulator every run clones | [`docs/surfaces/golden-simulator.md`](docs/surfaces/golden-simulator.md) |
| The tasks and what counts as solved | [`tasks/tasks.json`](tasks/tasks.json), [`docs/tasks.md`](docs/tasks.md) |
| How a screenshot is graded | [`docs/judging.md`](docs/judging.md), [`judge.py`](runner/judge.py) |
| What an edition freezes | [`docs/editions.md`](docs/editions.md), [`configs/editions.json`](configs/editions.json) |

[`docs/`](docs/README.md) has the rest: architecture, running, troubleshooting, the report and the
history of the harness.

### Telling us what is wrong

Open an issue or a pull request against this branch. The things we most want to hear:

- your tool is installed, configured or invoked in a way you would not recommend;
- the agent is missing guidance your tool expects it to have, or is given guidance you would not ship;
- a newer version should be the one measured;
- something in the harness (the preamble, tool loading, the preload rule, the timeout, the judge)
  favours one tool over another.

## August 2026 edition: what the numbers say

These are the published results of the previous edition: gpt-5.4-mini and haiku-4.5 on OpenCode 1.4.3,
argent 0.15.0 and agent-device 0.17.6, judged by GPT-5.4. Nothing here is compared with the November
2026 edition, which changes the harness, the models, the tool versions and the judge.

720 runs: 4 model configurations x 3 tool conditions x 60 tasks, one attempt each.

| Model | Tool | Completion | Cost / run | Time / run |
| --- | --- | ---: | ---: | ---: |
| haiku-4.5 (high) | argent | 97.5% | $0.219 | 75s |
| gpt-5.4-mini (high) | argent | 95.0% | $0.134 | 76s |
| haiku-4.5 (low) | argent | 94.2% | $0.195 | 66s |
| gpt-5.4-mini (high) | agent-device | 85.0% | $0.096 | 121s |
| haiku-4.5 (low) | agent-device | 84.2% | $0.131 | 142s |
| haiku-4.5 (high) | agent-device | 83.3% | $0.153 | 133s |
| gpt-5.4-mini (low) | argent | 82.5% | $0.139 | 90s |
| gpt-5.4-mini (low) | agent-device | 82.5% | $0.059 | 80s |
| gpt-5.4-mini (high) | no tool | 45.8% | $0.618 | 668s |
| gpt-5.4-mini (low) | no tool | 15.8% | $0.101 | 128s |
| haiku-4.5 (low) | no tool | 15.0% | $0.584 | 558s |
| haiku-4.5 (high) | no tool | 11.7% | $0.620 | 583s |

Completion scores a success as 1, a partial as 0.5 and a failure as 0.

The headline is that the tool dominates the model. Averaged over every model, agents scored 92.3%
with argent, 83.8% with agent-device and 22.1% with no device-control tool at all. Turning
reasoning effort up doesn't rescue a bad surface: haiku-4.5 on high effort with no tool finishes
11.7% of tasks, taking 583 seconds and $0.62 a run to do it, while the same model on *low* effort
reaches 84.2% with agent-device and 94.2% with argent - for a third of the cost or less, and in 66
seconds a run in argent's case. The cheapest cell in the whole table (gpt-5.4-mini low + agent-device, $0.059)
beats the most expensive one (gpt-5.4-mini high + no tool, $0.618) by 37 points of completion at a
tenth of the cost.

The no-tool baseline is not a strawman. Those agents get a POSIX shell, the full Xcode
command-line toolchain and `xcrun simctl` - screenshots, launch, terminate, `openurl`, `ui`, the
lot - described neutrally, with no suggestion of how to use it. What they don't get is a way to make
it pay off. Three of the four no-tool cells grind: a median of 67-71 shell calls per task, up to 197
in one run, and 27 of 60 runs hitting the 15-minute wall in the gpt-5.4-mini high cell alone. The
fourth fails the opposite way - gpt-5.4-mini on low effort gives up early, a median of 19 calls in 94
seconds, and lands at 15.8%. Grinding and quitting both end in the same place.

## What a task looks like

Tasks live in [`tasks/tasks.json`](tasks/tasks.json). Each one pairs the
prompt the agent is given with an exact prose description of the screen that counts as solved:

```json
{
  "id": "bsky-01",
  "app": "bluesky",
  "kind": "nav",
  "prompt": "Open the \"Feeds\" tab (next to Following) at the top of the Bluesky app.",
  "solved_screen": "The Feeds screen is open with the header \"Feeds\" centered at top (back arrow left, gear icon right). Below are the sections \"My Feeds\" ... Bottom tab bar shows Home, Search, Chat, Notifications, Profile.",
  "nav_category": "tab-switch"
}
```

The set that runs is 60 tasks over two apps - 30 in [Bluesky](https://github.com/bluesky-social/social-app)
1.122.0 and 30 in [Element iOS](https://github.com/element-hq/element-ios) 1.11.40 - split across
navigation (39), interaction (13) and composing/posting (8), and tagged by category so the report can
break results down by what the task actually demanded: tab switches, drawer menus, search, opening an
item, settings navigation, reactions, form entry, compose.

Prompts are tool-agnostic. The harness prepends a thin preamble naming the app, the device and the
tool's interaction surface, then gets out of the way and lets each tool's own skills describe how to
drive a phone - which is what a real agent would have.

## How a run works

Every single run gets a clean world:

- **A fresh device.** Each run clones a golden simulator, uses the clone, and deletes it. No run
  ever inherits another run's device state. Clone cleanup uses an exact, device-set-scoped ownership
  journal; unrelated simulators are recorded as timing context but never shut down or deleted.
- **A fresh process tree.** Processes proven to belong to the benchmark are killed and *verified*
  dead at both ends of every run; unrelated matches are reported and preserved. A host-wide lock
  means exactly one bench stream exists at a time.
- **Fresh server state.** Per-app reset hooks roll back the backend the app talks to, so a post made
  in one run can't change the screen another run sees.
- **Nothing of the host's.** Agent instructions, skills and MCP servers configured on the host are
  kept out of every run, or stop it before it starts, so a run's system prompt is the same on any machine.
- **Pinned everything.** The OpenCode version, the tool versions and the app versions are pinned in
  `configs/`, checked against what's installed, and stamped into each run's metadata. The
  runner warns on drift rather than silently producing results that don't match the version they
  claim, and a result is filed under the tool version that actually ran.

Every selected tool gets the same version, guidance-manifest and source-revision provenance checks.
Lifecycle differences are capability-based and explicit: argent and Mobile MCP run inside OpenCode's
process group (Mobile MCP's `mobilecli` daemon gets a per-run home and is killed at teardown), while
agent-device's detached daemon gets a per-run state directory and exact stop command. These
differences do not change task timing or model permissions.

Scoring is a separate, resumable pass. A vision model (GPT-6.1 Sol at temperature 0 in the November
2026 edition, GPT-5.4 before it) sees the final screenshot, the task the agent was given, the
description of the solved screen and the list of actions taken, and returns success / partial / fail.
The prompt is explicit that the task text is the authority: an agent is never marked down for skipping
something it was never asked to do. Same judge, same prompt, every cell.

An **edition** is one frozen setup: harness, OpenCode version, models and judge. Results are compared
only inside an edition, and each edition's runs live in their own directory
(`results/editions/<id>/`; the August 2026 edition's sit directly in `results/`).

## Running it yourself

You'll need macOS with Xcode and the Simulator, Python 3.12+, Node, [opencode](https://opencode.ai)
2.3.0 with API keys in its auth store, and whichever tools you want to benchmark on `PATH`
([`docs/tools.md`](docs/tools.md#version-pins) has the install lines). The target
apps have to be installed and seeded first - `tasks/setup/` holds the per-app scripts, and
Bluesky additionally needs its Metro dev server running, since it's an Expo dev client
([`docs/surfaces/`](docs/surfaces/)). Then build the golden simulator once
(`tasks/setup/golden/make_golden.sh`) and:

```bash
APPS=bluesky,element ./run_all.sh
```

That preflights the surface before spending anything on API calls, self-heals the ledger, runs every
pending cell, judges the screenshots and prints final coverage. It is resumable - re-running picks up
exactly what's missing. Narrow it with environment variables:

```bash
ONLY=haiku55_low,gpt6luna_high ./run_all.sh   # restrict models
TOOLS=argent,mobile-mcp ./run_all.sh          # restrict tools
APPS=bluesky ./run_all.sh                     # restrict apps
SKIP_JUDGE=1 ./run_all.sh                     # run the matrix without scoring
```

For a single run, or to inspect the plan, use the runner directly:

```bash
python3 runner/bench.py --list
python3 runner/bench.py --cell haiku55_low:mobile-mcp --task bsky-01
python3 runner/doctor.py                 # health, coverage and ledger check
```

Machine-specific paths resolve through `bench_env.py` (env override, then auto-detect, then a
documented fallback), so there are no constants to edit before your first run. On a shared machine,
call `bench.py` directly rather than `run_all.sh` - the latter assumes a dedicated host and uses a
full process-kill scope.

## Repo layout

```
run_all.sh             one command: preflight -> run -> judge -> coverage
runner/                the harness: matrix runner, isolation, preload, judge, doctor, report export
configs/               per-tool opencode configs, the editions, pinned OpenCode, tool and app versions
tasks/                 tasks.json plus per-app seeding and the golden-simulator builder
docs/                  how the harness, the tools, the tasks, the judge and the report work
results/               every run: final.png, transcript.jsonl, meta.json, score.json
web/                   the report site (Preact + Vite, prerendered)
```

The report site is built from `results/` - `npm run build` in `web/` exports the JSON, re-encodes each
run's screenshot to webp (needs Pillow) and prerenders the report and the run explorer into `public/`.
Transcripts are stored without the screenshots OpenCode embeds in tool results, and with the bench
host's home directory written as `~`.

## Caveats worth knowing

- **One attempt per cell.** Each (model, tool, task) gets one shot. Run-to-run variance is real and
  this matrix does not measure it, so treat small gaps between neighbouring rows as noise and read the
  large ones.
- **Two apps.** Bluesky and Element are real, complex, unmodified apps, but they are two apps. The
  task file holds tasks for three more apps that are not run in this edition.
- **Simulator, not hardware.** Everything runs on the iOS Simulator.
- **One judge.** A single vision model grades every run. It is consistent across cells, which is
  what fairness needs here, but it is not infallible - the run explorer publishes every screenshot
  and verdict so you can disagree with a specific call.
- **A moving target.** Models and tools both ship fast. Every number here is tied to pinned versions,
  which is why they're recorded per run rather than in a footnote.

Built by [Software Mansion](https://swmansion.com/).
