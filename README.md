# AppControlBench

How well can an LLM agent actually operate a real iOS app - and how much of that ability comes
from the model versus the tool you hand it?

This repo is the benchmark we built to answer that. It runs coding agents against real, unmodified
iOS apps in the Simulator, gives them a plain-language task ("open the Feeds tab", "in the Book Club
room, reply to one of the existing messages with the text 'sounds good'"), and grades the final
screenshot. Every run is isolated, every screenshot and transcript is published, and the whole
matrix is reproducible from this repo.

**Results, run explorer and per-run screenshots: [appcontrolbench.swmansion.com](http://appcontrolbench.swmansion.com/)**

## What the numbers say

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

**Disclosure:** [argent](https://github.com/software-mansion/argent) is built by Software Mansion, who also built this benchmark. That is exactly why every run's screenshot, transcript, prompt, verdict and timing ships in this repo - so you can
check the result rather than take our word for it. [callstack/agent-device](https://github.com/callstack/agent-device) is driven through its own shipped skills the way its authors intend.

## What a task looks like

Tasks live in [`benchmarks/tasks/tasks.json`](benchmarks/tasks/tasks.json). Each one pairs the
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

The current set is 60 tasks over two apps - 30 in [Bluesky](https://github.com/bluesky-social/social-app)
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
  ever inherits another run's device state.
- **A fresh process tree.** All bench processes are killed and *verified* dead at both ends of every
  run, and a host-wide lock means exactly one bench stream exists at a time. A second invocation
  fails loudly instead of quietly interleaving.
- **Fresh server state.** Per-app reset hooks roll back the backend the app talks to, so a post made
  in one run can't change the screen another run sees.
- **Pinned everything.** Tool versions (argent 0.15.0, agent-device 0.17.6) and app versions are
  pinned in `benchmarks/configs/`, checked against what's installed, and stamped into each run's
  metadata. The runner warns on drift rather than silently producing results that don't match the
  version they claim.

Scoring is a separate, resumable pass. A vision model (GPT-5.4 at temperature 0) sees the final
screenshot, the task the agent was given, the description of the solved screen and the list of
actions taken, and returns success / partial / fail. The prompt is explicit that the task text is
the authority: an agent is never marked down for skipping something it was never asked to do. Same
judge, same prompt, every cell.

## Running it yourself

You'll need macOS with Xcode and the Simulator, Python 3.12+, Node, [opencode](https://opencode.ai)
with API keys in its auth store, and whichever tools you want to benchmark on `PATH`. The target apps
have to be installed and seeded first - `benchmarks/tasks/setup/` holds the per-app scripts, and
Bluesky additionally needs its Metro dev server running, since it's an Expo dev client. Then build
the golden simulator once (`benchmarks/tasks/setup/golden/make_golden.sh`) and:

```bash
cd benchmarks
./run_all.sh
```

That preflights the surface before spending anything on API calls, self-heals the ledger, runs every
pending cell, judges the screenshots and prints final coverage. It is resumable - re-running picks up
exactly what's missing. Narrow it with environment variables:

```bash
ONLY=gpt_high,haiku_low ./run_all.sh   # restrict models
TOOLS=argent ./run_all.sh              # restrict tools
APPS=bluesky ./run_all.sh              # restrict apps
SKIP_JUDGE=1 ./run_all.sh              # run the matrix without scoring
```

For a single run, or to inspect the plan, use the runner directly:

```bash
python3 runner/bench.py --list
python3 runner/bench.py --cell gpt_high:argent --task bsky-01
python3 runner/doctor.py                 # health, coverage and ledger check
```

Machine-specific paths resolve through `bench_env.py` (env override, then auto-detect, then a
documented fallback), so there are no constants to edit before your first run. On a shared machine,
call `bench.py` directly rather than `run_all.sh` - the latter assumes a dedicated host and uses a
full process-kill scope.

## Repo layout

```
benchmarks/
  run_all.sh           one command: preflight -> run -> judge -> coverage
  runner/              the harness: matrix runner, isolation, judge, doctor, report export
  configs/             per-tool opencode configs, pinned tool and app versions
  tasks/               tasks.json plus per-app seeding and the golden-simulator builder
data/                  every run: final.png, transcript.jsonl, meta.json, score.json
website/               the report site (Preact + Vite, prerendered)
public/                built site, exported report JSON and per-run webp screenshots
```

The report site is built from `data/` - `npm run build` in `website/` exports the JSON, re-encodes
each run's screenshot to webp (needs Pillow) and prerenders both the report and the run explorer.
Pushes deploy to Vercel through `.github/workflows/deploy-vercel.yml`.

## Caveats worth knowing

- **One attempt per cell.** 720 runs is one shot at each (model, tool, task). Run-to-run variance
  is real and this matrix does not measure it, so treat small gaps between neighbouring rows as
  noise and read the large ones.
- **Two apps.** Bluesky and Element are real, complex, unmodified apps, but they are two apps. The
  task file is built to grow, and results should be re-read as it does.
- **Simulator, not hardware.** Everything runs on the iOS Simulator.
- **One judge.** A single vision model grades every run. It is consistent across cells, which is
  what fairness needs here, but it is not infallible - the run explorer publishes every screenshot
  and verdict so you can disagree with a specific call.
- **A moving target.** Models and tools both ship fast. Every number here is tied to the pinned
  versions above, which is why they're recorded per run rather than in a footnote.

Built by [Software Mansion](https://swmansion.com/).
