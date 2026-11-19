# Editions

An **edition** is one frozen benchmark setup: a harness generation, an OpenCode version, a model roster and
a judge. Two results are comparable only inside one edition. The same tool version scored under another
harness, on other models or by another judge is a different measurement, so the runner keeps editions apart
on disk and the site shows one at a time.

    2026-11   isolation-v5              OpenCode 2.3.0   haiku-5.5, gpt-6-luna            judge gpt-6.1-sol
    2026-08   isolation-v3-progressive  OpenCode 1.4.3   gpt-5.4-mini, haiku-4.5, qwen    judge gpt-5.4
    2026-06   (no harness stamp)        OpenCode 1.4.3   unranked

The tool version is the axis *inside* an edition ([tool-versions.md](tool-versions.md)): to compare a new
argent with an old agent-device, run both in the same edition.

## Why

Before editions the site had one pool of runs. A tool version's headline score was the average over
whichever models that version had been run on, so two versions recorded months apart, on different models,
were ranked against each other. Harness changes were handled by marking every older run *stale* and
re-running it in place, which destroys the earlier data and leaves a tool version nobody re-runs showing a
flagged leftover next to current numbers. And a model key recorded no checkpoint, so repointing `haiku_low`
at a newer Haiku would have mixed two models in one row.

## The rule

> **Everything in an edition is comparable. Nothing is compared across editions.**

Start a new edition whenever any of these changes: `bench.HARNESS`, the pinned OpenCode version, the model
roster, or the judge. Never edit a past edition's entry, and never repoint a model key: a new checkpoint
gets a new key (models.md).

A new tool version does **not** start an edition. It is run on the current edition's roster, which is what
makes it comparable with every version already there.

## `configs/editions.json`

```jsonc
{
  "current": "2026-11",                      // where new runs go
  "editions": [                              // newest first
    { "id": "2026-11", "label": "November 2026",
      "results": "results/editions/2026-11", // its own results tree
      "harness": "isolation-v5", "opencode": "2.3.0", "judge": "gpt-6.1-sol",
      "models": ["haiku55_low", "haiku55_high", "gpt6luna_low", "gpt6luna_high"] },
    { "id": "2026-08", "label": "August 2026", "results": "results",
      "harness": "isolation-v3-progressive", "opencode": "1.4.3", "judge": "gpt-5.4" },
    { "id": "2026-06", "label": "June 2026", "results": "results", "harness": null }
  ]
}
```

| Key | Means |
| --- | --- |
| `current` | The edition new runs are written to and stamped with. |
| `results` | The edition's results directory. `bench.RESULTS` is the current edition's. |
| `harness`, `opencode`, `judge` | What the edition froze. `test_editions.py` fails if `bench.HARNESS`, the OpenCode pin or the judge disagree with the current edition. |
| `models` | The roster: what a bare matrix runs, and the columns the site shows before any result exists. A past edition shows the models it has runs for. |

`runner/editions.py` reads the file; nothing else parses it.

## On disk

A run is written to `results/editions/<edition>/<model>__<variant>/<task>/` and stamped
`versions.edition` in its `meta.json`. Each edition directory is a whole results tree, with its own
`ledger.json`, so the ledger, the judge, the doctor and the dashboard work inside the current edition
unchanged. A new edition starts empty: every unit is pending, and nothing in an older edition is
overwritten.

Results recorded before editions existed stay where they are, in the bare `results/` tree. The editions that
name that directory share it, and a run there belongs to the edition whose `harness` its own stamp carries.
The edition with `harness: null` takes whatever is left: runs from before harness stamps existed. Those stay
browsable and are never scored.

## The runner

- `bench.EDITION` is the current edition; `bench.RESULTS` is its directory.
- `run_matrix.PLAN` is its `models`. `ONLY=` narrows a pass to some of them; a model outside the roster
  is refused, and a dashboard queue entry for one is skipped. To run another model in the edition, add
  it to `models`, and run every tool on it.
- `judge.py` scores the current edition with its `judge`. `--edition <id>` scores another one with that
  edition's harness and judge: verdicts from two judges are not comparable, so a past edition keeps the
  judge it was scored with.

## The site

`runner/report_data.py` exports one dataset per edition, under `data/v1/editions/<id>/`, and
`data/v1/editions.json` listing them. An edition with no runs is left out. The site opens on the newest
edition that has a scored run, so an edition that has only just started recording does not take the front
page with an empty table.

Each edition is prerendered to its own copy of the pages: the default one at the site root, every other
under `/e/<id>/`. The **Edition** control beside the tool-version selects navigates between them. Model
list, tool versions, every average and the run explorer are those of the selected edition; a run from
another edition cannot appear, because it is not in the page's data.

Inside an edition a run is *stale* only if it does not carry that edition's harness, which in practice means
the catch-all edition's runs.

## Publishing

`publish.yaml`'s model gate applies to both layouts: a cell under `results/editions/<id>/` ships only if its
model is listed, exactly like one under `results/`. An edition whose models an environment does not carry has
no runs there and so does not appear on that site (publishing.md).

## Starting an edition

1. Make the change that calls for one (harness, OpenCode pin, new model keys in `bench.MODELS` /
   `bench.EFFORT` / `bench_models.py`, judge).
2. Add the edition at the top of `configs/editions.json` and point `current` at it.
3. `python3 runner/test_editions.py` checks the runner agrees with it.
4. Add its model keys to the environments in `publish.yaml` that should show it.
5. Run the matrix, then the judge. To compare against an older tool version, install that version and run
   it in the new edition as well.
