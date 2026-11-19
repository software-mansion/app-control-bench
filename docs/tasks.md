# Tasks

All tasks live in a single file: **`tasks/tasks.json`**. There are **150 tasks across 5 apps** - bluesky /
element / expensify / immich / mastodon 30 each.

**Authored is not the same as runnable.** Only the apps enabled in `bench.APPS` run, so the matrix is
currently **90 tasks**: bluesky 30 + element 30 + mastodon 30. Immich and Expensify are authored but
[not wired in](surfaces/README.md#status).

```bash
python3 runner/bench.py --list    # the runnable set - the number to trust
```

## Schema

```json
{
  "id": "bsky-01",
  "app": "bluesky",
  "kind": "nav",
  "needs_auth": true,
  "prompt": "Open the \"Feeds\" tab (next to Following) at the top of the Bluesky app.",
  "solved_screen": "The Feeds screen is open with the header \"Feeds\" centered at top ...",
  "nav_category": "tab-switch"
}
```

| Field | Meaning |
| --- | --- |
| `id` | `<app>-NN`, `01`-`30`. Prefixes: `bsky`, `element`, `expe`, `immich`, `mast`. |
| `app` | Which surface it runs against. |
| `kind` | `nav` (93) · `interact` (42) · `post` (15). |
| `needs_auth` | Currently **`true` for all 150** - every surface is behind a login. |
| `prompt` | What the agent is told to do. **Tool-agnostic** - the tool-specific `how` clause comes from the [preamble](harness.md#the-preamble). |
| `solved_screen` | Prose description of what the correct final screenshot looks like. **The single criterion the judge scores against** - it must describe exactly the end state a literal execution of `prompt` produces, no more and no less. |
| `nav_category` | Navigation category. `bench.py` stamps it into every `meta.json`; see below for what does and does not consume it. |
| `annulled` / `annulled_reason` | Optional. Excludes the task from runs and reports (currently unused - no tasks are annulled). |

## Navigation categories

`nav_category` is the intended breakdown axis and every task carries one, but **nothing consumes it yet**:
`bench.py` records it in each run's `meta.json` and `report.py` never reads it. The report's only
task-shaped control is the per-task grid's `type` filter, which pivots on `kind`.

| Category | Count |
| --- | --- |
| `open-item` | 38 |
| `settings-nav` | 24 |
| `tab-switch` | 21 |
| `compose` | 17 |
| `search` | 13 |
| `react` | 12 |
| `menu-drawer` | 11 |
| `form-entry` | 11 |
| `auth-nav` | 2 |
| `scroll` | 1 |

## Writing a task: `solved_screen` must match the prompt, and both must match reality

This is the single easiest way to corrupt the benchmark, and it has bitten us repeatedly.

**`solved_screen` must be exactly the end state the `prompt` produces.** It is the only criterion the judge
scores against, and the reader of the report only ever sees `prompt` - so any drift between the two is both
unfair to the agent and invisible to the human. Never write `solved_screen` by describing a screenshot from a
run that happened to pass: that canonizes whatever the agent did, including steps nobody asked for. Derive it
from the prompt.

- **Never demand a state the prompt did not ask for.** If the prompt says *find* the theme preferences, do not
  require the theme picker to be *open* - that is an extra tap, and an agent that obeyed the prompt exactly
  gets marked down for it. (This was `element-10`.)
- **Never demand a transient state.** Toasts and video HUDs auto-dismiss; require the durable result instead.
- **Where the prompt is permissive, stay permissive.** "react with an emoji (for example a thumbs-up)" must not
  become "a thumbs-up reaction".
- **Where the prompt is specific, be specific.** "Compose *and publish*" must not accept an unsent composer.

**The criterion must also describe what the app can actually show.** The self-hosted backends cannot render
every state a hosted product can. If `solved_screen` describes a screen the backend is incapable of producing,
the vision judge fails a correct agent - and the signature is a suspicious wall of `partial` verdicts rather
than an obvious error.

Known backend limits, all now reflected in `tasks.json`:

**Bluesky (self-hosted PDS/AppView):**
- **Chats/DMs** - there is no chat service. The Chats tab always shows *"Whoops! could not resolve iss did"*
  with a Retry button. **That error screen is the correct navigation end state** (`bsky-13`).
- **Feeds** - the PDS has no custom feeds, so the Feeds tab shows "No results found" (`bsky-15`), and the
  only saved feed is "Following" (≈ home).
- **Hashtags** - no dedicated hashtag-feed page; tapping or searching a hashtag lands on Explore search
  results (Top tab). The seeded tag is `#caturday` (`bsky-16`).

**Element (native, self-hosted Synapse):**
- The create menu is Start chat / Create room / Create a space / Explore rooms - there is **no "Join Room"**
  (`element-06`).
- The account menu (tap avatar) is User settings / Invite friends / Feedback / Sign Out - it shows **no
  display name or user ID** (`element-20`).
- There is no separate "Help & About" screen; the version lives in the Settings **ABOUT** section
  (`element-19`).
- `element-16..19` (Notifications / Security / Sessions / About) are **sections of one long Settings page**,
  not distinct screens. The vision judge accepts the section; a stricter judge would not.

The general rule: **Task = Judge = what the UI actually does.** When they drift apart, fix the task, then
clear the affected `score.json` files so the runs get re-judged. (This is exactly what commit `e12911c` did
for `bsky-03`, whose criterion said "photography" while the prompt searched "cat".)

> The legacy `success` field was removed: it duplicated `solved_screen` in 145/150 tasks, and where the two
> disagreed it silently gave the judge two different answers. `solved_screen` is now the single criterion.

## Task history worth knowing

Element `11`/`12`/`13` originally targeted the Favourites / People / Unreads room-list **filter chips**.
Those are an **Element X** feature and do not exist in Element Classic, so they were rewritten against real
affordances: `11` = open a room's Threads view, `12` = long-press a room → Notifications settings, `13` =
open a member's profile. "Favourite a room" was rejected outright as a task because Element Classic shows no
visible favourite indicator - there would be nothing for the judge to see.

All 30 element tasks were audited on-device against the live UI on 2026-07-06.
