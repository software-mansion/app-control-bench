# Documentation

Everything about this benchmark lives here. `README.md` at the repo root is deliberately four lines long.

## Start here

| | |
| --- | --- |
| [**architecture.md**](architecture.md) | What the benchmark measures, where the code lives, and the per-run isolation lifecycle. **Read this first.** |
| [**running.md**](running.md) | `./run_all.sh`, the doctor, the queue, the dashboard, the ledger, every env var. |
| [**troubleshooting.md**](troubleshooting.md) | When it breaks. Start with `python3 runner/doctor.py`. |

## The benchmark

| | |
| --- | --- |
| [**harness.md**](harness.md) | How the agent is configured: progressive-disclosure skills, ambient suppression, the preamble, the effort proxies. The integrity rules. |
| [**tools.md**](tools.md) | argent (MCP) · agent-device (CLI) · `none` (the no-tool control). Version pins. |
| [**tool-versions.md**](tool-versions.md) | Tracking several versions of a toolkit: the one on-disk rule, and what the site shows by default. |
| [**editions.md**](editions.md) | Which results may be compared: one frozen harness, OpenCode version, model roster and judge per edition. When to start a new one. |
| [**tasks.md**](tasks.md) | The 150 tasks: schema, navigation categories, and why `solved_screen` must match both the prompt and what the app can actually render. |
| [**judging.md**](judging.md) | The vision judge (the current edition's: GPT-6.1 Sol). What it skips and why. |
| [**reporting.md**](reporting.md) | The exporter, the Vite/Preact build, the theme, the `stylized` branch. |

## The surfaces (apps under test)

| | |
| --- | --- |
| [**surfaces/**](surfaces/README.md) | Overview, status table, the two kinds of reset, how to add one. |
| [**surfaces/golden-simulator.md**](surfaces/golden-simulator.md) | The logged-in simulator image every run clones. **Read before touching any surface.** |
| [bluesky](surfaces/bluesky.md) · [element](surfaces/element.md) · [mastodon](surfaces/mastodon.md) | Wired in and running. |
| [immich](surfaces/immich.md) · [expensify](surfaces/expensify.md) | Built but not wired · notes only. |

## Operations

| | |
| --- | --- |
| [**history.md**](history.md) | What was tried, what broke, which datasets were thrown away and why. **Read before re-litigating a decision.** |

## The short version

An LLM agent is given a native iOS app and a task ("open the Feeds tab"). We vary the **model** and the
**device-control tool**, hold everything else constant, and have a vision judge score the final screenshot.

Every run is fully isolated: a fresh clone of a logged-in golden simulator, a server-side reseed, verified
process teardown at both ends, one device at a time. Results land in
`results/editions/<edition>/<model>__<variant>/<task>/` and render into the static report built by `web/`.
