# Versioned toolkits

The benchmark compares **toolkits** (`argent`, `agent-device`, the `none` control). A toolkit ships new
versions, and a version is a different tool: argent 0.21.0 is not argent 0.15.0 with a bug fixed, it is a
different set of affordances handed to the agent. So the axis the site compares is not the toolkit, it is
the **tool variant** — one version of one toolkit.

    argent@0.21.0      argent@0.15.0      agent-device@0.17.6      none

The site reads this, the runner writes it, and the results tree is laid out by it.

Versions are compared **inside an edition** ([editions.md](editions.md)): one harness, OpenCode version,
model roster and judge. Two versions recorded in different editions are different measurements, and the
site never puts them in one table. To compare them, run both in the same edition.

---

## The rule

> **A result directory carries the tool's version: `<results>/<model>__<toolkit>@<version>/<task>/`.**

`<results>` is the edition's results directory - `results/editions/<edition>/` for the current one, the
bare `results/` for the editions that predate the layout.

The directory name is the only thing the version changes. `meta.json` is untouched by it: `tool` is still
the toolkit (`"argent"`), and `versions.tool` is still the version stamp `bench.run_versions` has written
since schema 2. The directory is derived from that stamp, never the other way round.

    results/gpt_low__argent@0.21.0/bsky-01/     meta.json: {"tool": "argent", "versions": {"tool": "0.21.0"}}

A toolkit with no version to report — the `none` control — keeps its bare directory, `results/<model>__none/`.

### Why the directory and not a new field

The version is already in the data: every `meta.json` since schema 2 stamps `versions.tool` with the
version that actually ran, and the exporter reads exactly that. What is missing is **space on disk**.
`results/<model>__argent/` is one directory per (model, toolkit), so the first argent 0.21.0 run overwrites
the argent 0.15.0 result for that task, and the comparison the versions exist for is destroyed by the act of
producing it. Naming the directory after the variant is what stops that — the version is not new
information, it is the information that has to reach the filesystem.

### How the runner writes it

`runner/ledger.py` owns the name. `variant_id(toolkit, version)` builds it, `split_variant` takes it apart,
`run_variant(meta)` reads it off a run, and `cell_name`/`unit_id`/`result_dir` are the layout:

    ledger.cell_name("gpt_low", "argent@0.21.0")    ->  gpt_low__argent@0.21.0
    ledger.unit_id("gpt_low", "argent@0.21.0", …)   ->  gpt_low__argent@0.21.0__bsky-01

A **unit** is therefore `(model, variant, task)`, not `(model, toolkit, task)`. `bench.tool_variant(tool)`
resolves a toolkit to the variant this machine would produce right now, from the `detect_tool_version` the
runner already calls for the drift check (`None` for `none`, giving the bare id for free), and `run_one`
writes into that directory and books the unit against it. `bench.TOOLS` stays a list of toolkits: what you
*run* is a toolkit, what you *record* is the variant that ran.

That the unit is keyed by variant is what makes a version bump legible: after one, every unit of the new
variant is `pending`, because none of them has been run. A bump is a fresh matrix, not an overwrite — and
real API spend, so bump `configs/tool-versions.json` deliberately.

### Trees written before the layout

`ledger.result_dir` falls back to the bare toolkit directory when it holds the exact version being asked
for. Without it every run in an un-migrated tree reads as unrun and the next matrix regenerates all of it —
and un-migrated trees are real: the public repo, and any live run dir not migrated with the internal one.

`runner/migrate_variants.py` moves a tree onto the layout. It is idempotent, refuses to overwrite, and takes
each run's destination from that run's own `meta.json`, so a directory can never come to claim a version its
runs do not carry. It re-keys `results/ledger.json` in the same pass.

    python3 runner/migrate_variants.py --results results          # dry run: print the plan
    python3 runner/migrate_variants.py --results results --apply

---

## What the site does with it

`runner/report_data.py` keys every row on the variant id, taken from each run's own stamp:

```
meta.versions.tool == "0.15.0"   ->  argent@0.15.0
meta.versions.tool == null       ->  argent          (the `none` control, or a pre-stamp legacy run)
```

Because the key comes from `meta.json` and not from the directory name, the reader is indifferent to which
layout a tree is on — the directory rule protects future *writes*, and is not a precondition for reading.

- Every variant is published. A second version adds a column; it never redefines the one already there.
- One variant per toolkit is shown by default: the **newest recorded version** in the edition being
  shown. The rest are carried and hidden until a reader picks them from the version control on either
  page. Only versions with runs in that edition are offered.
- A run whose version was never recorded lands on the bare toolkit id rather than borrowing a version it
  cannot support. Those are pre-isolation runs, already `stale` and already out of every aggregate.
- Versions sort by their digits, newest first (`0.21.0` above `0.15.0` above `0.9.0`). Keep versions
  dotted-numeric.

### `configs/tool-versions.json`

The pin file gained an optional object form. A bare string still means "this is the pin":

```jsonc
{
  "argent": "0.21.0",                                 // pinned; the site leads with the newest recorded
  "agent-device": { "pinned": "0.19.0", "default": "0.17.6" }
}
```

| Key | Read by | Means |
| --- | --- | --- |
| `pinned` | `bench.py` | The version new runs should be produced with; it warns on drift. |
| `default` | `report_data.py` | Lead with this version instead of the newest. Optional. |

`default` exists for one situation: a version whose runs are **part-way through recording**. Newest-wins
would put a column with 12 of 150 tasks in it at the head of the report. Naming the settled version as
`default` keeps the headline honest while the new one fills in, and costs nothing once it has.

---

## Adding a version, start to finish

1. Install the new toolkit version on the bench host.
2. Bump `pinned` in `configs/tool-versions.json` (add `default` if the old version should keep the lead
   while the new one records).
3. Re-verify the toolkit's host preload entry at the new version
   ([harness.md](harness.md#host-preload-one-time-tool-setup-outside-the-timer)): `python3 runner/preload.py
   <toolkit>` must end `built`, `present` or `none`. agent-device's entry refuses any version it was not
   verified for, so its runs stay pending until the entry is updated.
4. Run the matrix. Results land in the current edition, in `<model>__<toolkit>@<new>/`, beside the old
   version, not on it. They are on the same models, harness and judge as every other version in that
   edition, which is what makes the comparison valid.
5. Rebuild the site (`cd web && npm run build`). The new version appears as the default column; the old one
   is one select away.
6. When the new version is complete, drop `default` if you set it.

Nothing is deleted at any step. The old version stays published and selectable — that comparison is the
reason for versioning the axis in the first place.
