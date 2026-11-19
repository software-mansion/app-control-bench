# Judging

Judging is an **overlay on top of the runs, not part of them.** It only ever reads a run's `meta.json` and
`final.png` and writes a `score.json` beside them - it never touches the simulator, the device lock, or a
run's state. So it can be stopped, wiped and restarted at any time, including while the matrix is running,
without costing a single run completion. Erasing every verdict costs only the API spend to re-judge.

```bash
python3 runner/judge.py                      # resumable; scores every unjudged run
python3 runner/judge.py --rejudge            # re-score everything
python3 runner/judge.py --task bsky-01,bsky-02
python3 runner/judge.py --app element
python3 runner/judge.py --model gpt-5.4      # override the judge model
python3 runner/judge.py --edition 2026-08    # score another edition, with its own harness and judge
```

The judge and the results it scores are the **current edition's** ([editions.md](editions.md)). Verdicts
from two judges are not comparable, so changing the judge starts a new edition, and a past edition keeps the
judge it was scored with.

## The judge

**GPT-6.1 Sol with vision** in the 2026-11 edition (GPT-5.4 at temperature 0 before it). It sees exactly one image - the run's `final.png` - plus the task
context, and returns a one-line JSON verdict:

```json
{"verdict": "success|partial|fail", "confidence": 0.0-1.0, "reason": "..."}
```

`judge.py` calls the OpenAI API directly (not through OpenCode): `max_completion_tokens: 2000`
(`max_tokens` is rejected), the image as a base64 data URL, 5 retries with exponential backoff. It sends
`temperature: 0` and, when a model answers that it does not accept the parameter, resends at once without it
and stops sending it to that model. The key is read from OpenCode's credential store.

## The criterion: exactly two task fields

The prompt is built from the task's `app`, `prompt` and `solved_screen`, plus the run's `tool_names` and
`n_tool_calls`. **`prompt` and `solved_screen` are the whole criterion**, and they are handed to the judge
under explicit labels:

| Task field | Label in the judge prompt | Role |
| --- | --- | --- |
| `prompt` | `TASK GIVEN TO AGENT` | what the agent was actually asked to do - the authority on what was asked |
| `solved_screen` | `EXACT DESCRIPTION OF THE SOLVED SCREEN` | the visual spec the final screenshot is matched against |

They describe one and the same outcome: `solved_screen` is what the screen looks like once the `prompt`, and
only the `prompt`, has been carried out. The rubric tells the judge to use them together and, if they ever
appear to disagree, to treat the `prompt` as authoritative - an agent is never marked down for skipping
something its task never asked for. Keeping them entailed is a task-authoring duty, not the judge's job - see
[tasks.md](tasks.md#writing-a-task-solved_screen-must-match-the-prompt-and-both-must-match-reality).

There is no third field. A legacy `success` field used to be passed alongside these two; it duplicated
`solved_screen` in 145/150 tasks and, where the two disagreed, silently gave the judge two different answers.
It has been deleted from `tasks.json` and from the judge prompt.

## Judge policy

**Keyboard artifacts are not agent errors.** iOS auto-capitalizes the first letter and can add trailing
punctuation. On typed-text tasks, only **changed, extra or missing words** count as a mismatch. This rule is in
the base prompt because without it the judge failed correct runs for typing "Hello" when the task said "hello".

**Invisible results are graded from the actions.** For tasks whose outcome a screenshot cannot fully show
(scrolling, for instance), it is a success if the actions the agent took plainly accomplish the task.

## Judge telemetry in score.json

Every `score.json` the judge writes records **what the judge was given**, so a verdict can be audited long
after the fact (and so the report can show it) without re-deriving anything:

```json
{
  "verdict": "partial", "confidence": 0.96, "reason": "...",
  "judge_model": "gpt-5.4", "cell": "gpt_low:argent", "task": "element-10",

  "judge_input": {
    "app": "element",
    "prompt": "<TASK GIVEN TO AGENT>",
    "solved_screen": "<EXACT DESCRIPTION OF THE SOLVED SCREEN>",
    "tool_names": ["argent_gesture-tap", "..."],
    "n_tool_calls": 14
  },
  "judge_prompt": "<the full rendered prompt sent to the vision model, verbatim>"
}
```

`judge_input` is exactly the substitution the prompt was rendered from (including the fallback text used when a
task has no `solved_screen`), and `judge_prompt` is the rendered result, verbatim.

**Score files written before this existed have neither key.** The judge does not re-judge a run that already
has a valid verdict, so old `score.json` files keep their old shape until they are re-judged with `--rejudge`.
Anything reading these keys - the exporter does - must fall back to reconstructing them from `tasks.json` plus
the run's `meta.json`.

## What the judge skips (and why that matters)

- **Stale/polluted runs** - any run whose `meta.versions.harness` ≠ the current `bench.HARNESS` is skipped,
  since it is slated to be re-run and overwritten. `judge.py --stale` judges *only* those instead, so the
  report can show them as `stale - pending re-run` where nothing current exists. Each is judged against
  `tasks/tasks.json` as last committed before its `meta.ts` (recorded as `judge_input.tasks_rev`), because
  prompts have changed since: older runs of bsky-03 searched "photography", not today's "cat". It needs the
  full git history, so it fails on a shallow clone.
- **Annulled tasks** (`annulled: true`).
- Runs missing `meta.json` or `final.png`.

## Resumability

An existing `score.json` with a valid verdict (`success` / `partial` / `fail`) is kept unless `--rejudge`.
`error` verdicts are always retried. On a valid verdict the unit is marked `judged` in the ledger.

To force a re-judge of specific runs, delete their `score.json` and re-run (or use `--rejudge` with `--task` /
`--cell` to scope the blast radius) - that is the intended way to re-score after fixing a task's `prompt` or
`solved_screen`. Re-judging is real API spend: never re-judge the whole matrix to test a change.

## Judge history (why it is GPT-5.4)

The judge has been changed twice, and both changes invalidated data:

1. **`google/gemini-2.5-flash`** - the original. Worked, but was superseded.
2. **`gpt-4.1-mini` vision** - **a bad judge.** It could not reliably OCR the screenshots and produced both
   false positives and false negatives. When this was discovered, **every `score.json` in the repo was
   deleted** (509 locally, 124 on the bench host) and all runs reset to judgement-pending rather than ship
   scores nobody trusted.
3. **`gpt-5.4` vision @ temp 0, with per-task `solved_screen` descriptions** - current.

**2026-07-13: every verdict was erased again and the SoTA cells re-judged from scratch.** Not a model change -
the verdicts on `main` had accumulated from several judge generations and from callers nobody was tracking, so
they were no longer a single comparable set. All 508 `score.json` files were deleted and the whole matrix reset
to judgement-pending, then re-scored in one pass by one judge. **No run was touched**: the erase cost zero run
completions and the matrix kept running on radoniusz throughout.

Resetting verdicts needs one thing that is easy to get wrong: **`ledger.reconcile()` is monotonic and will not
demote `judged` back to `completed`.** Deleting the `score.json` files is not enough - the ledger keeps
asserting `judged` and the judge skips them. Rewrite the ledger entries from `ledger.disk_state()` directly and
drop the `verdict` field.

The report header no longer hardcodes the judge name: it reads the distinct `judge_model` values off the
`score.json` files it is rendering (falling back to `judge.JUDGE_MODEL` when nothing is scored yet), so the
displayed judge is always the one that actually produced the verdicts. If a mixed set is ever rendered, the
header lists every judge it found.

If you change the judge model, assume you have invalidated the existing scores and plan to re-judge.
