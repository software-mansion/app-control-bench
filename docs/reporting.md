# The report and the public site

## Generating it

```bash
cd web
npm install
npm run build                    # -> ../public/index.html + ../public/index-runs.html
```

`npm run build` runs four steps in order (all from `web/`):

1. `runner/report_data.py` reads every edition's results ([`editions.md`](editions.md)), applies the
   public/stale/annulled gates, redacts, and writes one dataset per edition: normalized JSON under
   `public/data/v1/editions/<id>/`, screenshots under `public/artifacts/<id>/`, and
   `public/data/v1/editions.json` listing them.
2. `vite build` produces the browser bundles and one shared hashed stylesheet.
3. `vite build --config vite.config.ssr.ts` produces the Node prerender entry.
4. `node ../.vite-ssr/prerender.js` derives the default report view, renders both pages with Preact, and
   writes `public/index.html` and `public/index-runs.html` - once per edition: the edition the site opens
   on at the root, every other under `public/e/<id>/`.

The exporter owns `public/data` and `public/artifacts` and **prunes** them: a run that stops being
eligible has its JSON and its screenshot removed, rather than left behind from an earlier build.

Exporter flags: `--results` (default `results/`), `--out` (default `public`).

Env filters: `REPORT_APPS=bluesky,element`, `REPORT_MODELS=gpt_low,gpt_high`, `BENCH_PUBLIC=1` (hides
`INTERNAL_MODELS`; a no-op on `main`, where that set is empty).

Output is no longer one self-contained file. The pages link a shared hashed stylesheet and a page-specific
bundle, and the Run Explorer fetches a run's detail and transcript only when you open them — which is what
took `index-runs.html` from 17 MB to roughly 30 KB over the wire.

## Live preview while iterating

```bash
cd web
npm run dev                      # Vite dev server, hot module replacement
```

Exports the data once, then serves `web/` with HMR. Editing a component or either CSS file updates the
browser without a rebuild; re-run `npm run dev` to pick up new benchmark results.

Aggregation and rendering live in TypeScript under `web/src/`. `web/src/shared/metrics.ts` holds
`deriveReportView`, the single implementation both the prerender and the browser call —
`docs/report-frontend-contract.md` explains why that matters.

## What the report contains

- **Evidence-first hero** - the strongest same-model tool lift, with the best configuration's completion,
  cost and time beside it. Hero, key findings, leaderboard, matrix, breakdowns, and scatter all cover the
  same fixed set of exporter-eligible models.
- **Leaderboard** - configurations ranked by strict success, with completion score, successful-run time,
  cost, and sample size kept beside the ranking.
- **Comparison matrix** - completion score, model × tool, with family-grouped parent/child rows (a model's
  effort ladder collapses under its parent).
- **Editions** - every page shows one edition, a frozen harness, OpenCode version, model roster and judge.
  The Edition select beside the tool-version controls navigates to another edition's own prerendered pages;
  it does not render when the export carries a single edition. Nothing on a page comes from another edition.
- **Tool versions** - the axis is a tool *variant*, one version of one toolkit (`argent@0.15.0`), so a new
  version of a toolkit is a column beside the old one and never a replacement for it. Both pages show the
  newest version of each toolkit and hide the rest behind a per-toolkit select ("all versions" puts them
  side by side); the control does not render at all for a toolkit that has only one version, which is the
  case on `main` today. Choosing a non-default version on the report page fetches `run-index.json` once and
  re-derives every number through the same `deriveReportView` the prerender ran - the default view still
  costs no request. See [`tool-versions.md`](tool-versions.md) for the data format.
- **Breakdowns** - completion score and average time per task, per tool (top) and per model (bottom). The
  per-model bars carry the provider's brand mark beside the model name (same monochrome `mlogo` marks as the
  hero table). There is no by-task-type or by-navigation-category breakdown - `runner/bench.py` records
  `nav_category` in every `meta.json`, but the report contract leaves it out deliberately
  (`web/src/shared/contract.ts`), so rendering it would be new scope rather than migration work.
  **Both per-model breakdowns include the `no tool` baseline** (`ScoringPolicy.baselineToolId`): no-tool runs
  still use the model and contribute to both its cost and its successful-run time. The per-tool panels, hero
  table, and scatter also keep the baseline.
  The panel titles are just `completion score` / `avg time / task`: the top pair is labelled with tool names and
  the bottom with model names, so a `· by tool` / `· by model` suffix only restates the axis.
- **Cost-efficiency scatter** - x = average price per task, y = completion score. **One dot per model × tool**
  (shape encodes the tool: argent = circle, agent-device = square, none = diamond), with a dashed connector
  tracing each model's effort ladder within a tool. It deliberately does *not* collapse the tools together -
  that was the point of the most recent commit.
- **Per-task grid** - every run, with its `final.png`, a query/filter panel, a coverage bar, and a modal
  showing the parsed transcript.
- **Methodology** - scoring definitions, judge input, benchmark scope, limitations, and exact provenance.

Scoring: **strict success** counts only `success`. **Completion score** assigns `success` = 1.0,
`partial` = 0.5, and `fail` = 0. `error` and unjudged runs are excluded from the denominator, not scored
0.0. An `error` is a judge failure (5 API retries exhausted, or an unparseable reply), not an agent failure,
so counting it as a zero would charge the model for the judge's flake.

The dashboard's `success rate` card is a **different, stricter number**: `success / judged`
(`runner/dashboard.py`), where a `partial` sits in the denominator and contributes nothing. A run set with
many partials therefore reads lower on the dashboard than in the report.

**Exclusions:** runs whose harness stamp ≠ the current one are marked `polluted`, rendered as a `stale`
chip, and excluded from every aggregate. The one exception is a Tool comparison cell (or its footer) with
no current-harness run: instead of `pending` it shows the newest older result - the column's own
older-harness runs, else the newest older version of the same toolkit that the page is not showing
(down to the unversioned runs). That score is marked `stale - pending re-run`, naming the version it came
from, and kept off the heatmap. A newer version never stands in for an older one. Annulled tasks are
excluded entirely.

**Costs** are summed from `step_finish.cost` in each run's `transcript.jsonl`. Local models show as `free`.

**Redaction:** embedded transcripts have the repo root rewritten to `<repo>`, and **any** `/Users/<name>`
to `~` - by pattern, because a transcript carries the paths of the host that ran it (usually radoniusz)
rather than the one exporting it.

## The theme: one file, no exceptions

**`runner/theme.css` is the only file in the repo allowed to contain a raw style value** - a colour, font
stack, font size, shadow or z-index. Everything else (`report.css`, `web/src/**`, `dashboard.py`,
`logos.py`) reaches them through `var(--token)`.

```
runner/theme.css        every raw value: palette -> semantic -> type -> geometry -> elevation -> motion -> layers
runner/report.css       the report's components. var() only, zero literals
runner/check_theme.py   the guard: exits non-zero on any hardcoded value. Run it by hand
```

Both stylesheets are imported by `web/src/styles/index.css`, and Vite emits **one shared hashed
stylesheet** that both pages link — replacing the 106 KB that used to be inlined into each page
separately. They stay in `runner/` so the `stylized` branch reskin remains a `theme.css` value swap and
`dashboard.py` can keep inlining the same file for its independent `--d-*` tokens.

- **Reskinning = editing `theme.css` and nothing else.** Changing the accent used to mean touching a CSS
  block, a Python dict and two JS maps.
- **Layered on purpose.** A component names a *meaning* (`--accent`, `--ok`), never a hue (`--p-navy`). That
  is why the dark theme can swap the accent from navy to mint without a single component knowing.
- **The heatmap does not follow the accent.** It has its own hue, `--heat`, defaulting to `--heat-classic`
  (`#2563eb`, the pre-navy accent). The heatmap is the one place a colour is a *scale* rather than an
  emphasis: it ramps to 62% tint (`--tw-heat-floor` + `--tw-heat-range`), and navy at 62% is already
  near-black, so the top of the scale collapses into a dark slab and the cell's own percentage (`--ink`)
  stops reading against it. The tweaks panel's **heatmap colour** control swaps `--heat` between
  `--heat-classic` and `--heat-navy` (= follow the accent). In dark both resolve to mint - dark's accent was
  never navy, so the switch is a deliberate no-op there, and the classic blue is never let onto the graphite
  ground (it is the exact contrast failure dark got its own accent to avoid).
- **The heatmap ramp has two shapes, and the exponential one is a tweak** (**exponential heat**, on by
  default). Scores bunch near the top, so the linear ramp paints almost every cell a solid blue and the
  scale stops discriminating. The tweak raises `--hm` (the cell's raw `score / top score`) to
  `--tw-heat-exp` (default `11`, live-tunable in the panel), which flattens the low end - most cells read as
  untinted and only the leaders reach full colour. It keeps the endpoints: the strongest cell still lands on
  the same 62% tint, and the floor drops to 0 (`--tw-heat-exp-floor`) because a tinted "weakest" cell is the
  thing the curve exists to get rid of. The curve lives in `report.css`, **not** in the `--hm` value, so the
  toggle repaints without re-rendering the table and the Python-rendered and JS-re-rendered tables agree. It
  is a separate rule, not a swap of the linear ramp's terms: a browser without CSS `pow()` drops that one
  declaration and keeps the linear heatmap rather than losing the cell background entirely (verified in
  Chromium and WebKit, both of which do resolve it).
- **The dashboard is a separate product**, not a theme of the report: an operator console (dark, monospace,
  never published). It collides on token *names* (`--bg`, `--ok`) but shares no values, so it gets its own
  `--d-*` block in `theme.css`. Do not map it onto the report's semantic tokens - it would repaint white.
- **Not tokenised on purpose:** one-off padding, margins, grid templates, media-query breakpoints. Those are
  intra-component layout, not theme; a `--sp-9px` used once is indirection, not a design system.
- **The gear button is not dressed by the tweaks.** It carries its own `--elev-btn`, `--accent-line` border
  and `--accent` glyph. It used to be `--card` on `--bg` (`#fff` on `#f6f7f9`) behind a `--line` hairline
  with `box-shadow:var(--tw-shadow)` - and `--tw-shadow` defaults to `none`, because the shadows *tweak* is
  off by default. So the one control that opens the tweaks panel had its elevation switched off by a setting
  *inside* that panel, leaving a white square with a ~1%-contrast border alone in the right margin:
  invisible. Nothing ever covered it - `z-index` was never the problem. Don't rebind it to `--tw-*`.

Run the guard before pushing:

```bash
python3 runner/check_theme.py     # exit 1 on any violation; run it by hand, there is no CI
```

Known debt, deliberately left alone: the type scale has **16 steps**, several within 0.5px of each other
(`--fs-4`/`--fs-5`, `--fs-6`/`--fs-7`, `--fs-8`/`--fs-9`) - accidental rather than designed. They are all in
one place now; collapsing them shifts text metrics, so it is a design decision to make on purpose, not a
side effect of a refactor.

## Deploying

The public site, [appcontrolbench.swmansion.com](https://appcontrolbench.swmansion.com), is built from a
tagged commit of this repository: `npm run build` in `web/`, served as static files. The tag is the
checkpoint the live site corresponds to.

## The `stylized` branch

`stylized` reskins the report as a **1-bit monochrome phosphor CRT terminal**. The look was arrived at by
hard iteration and the rules are firm:

- **1-bit colour + ordered dithering is the technique.** Tones are faked with 2px SVG alpha masks
  (`--dth-25/50/75`), never smooth midtones or alpha fills.
- **One hue per theme, no mixing.** Green phosphor by default; the amber tweak swaps the *whole* tube. Never
  amber-in-green. This is why the semantic green/amber/red verdict colours are gone.
- **Meaning by shape, not colour.** Verdicts are a filled / dithered / hollow square.
- **Sharp, never blurry.** No glow blobs, no bloom.
- **Utilitarian, no posing.** No fake telemetry chrome, no blinking cursors, no decorative glyphs. Only real
  information.
- Glitches are rare, sharp, and JS-random (not a CSS loop); horizontal disturbance is a low-amplitude
  `skewX` shear, not a jerk. `svg { shape-rendering: crispEdges }` so diagonals stair-step.

Fonts (VT323, Share Tech Mono) are embedded from `runner/fonts/` so the pages need no third-party request.

**When syncing `main` into `stylized`,** port the structural changes (e.g. new tweak variables) but **reject
anything that adds a second colour** - including `main`'s per-company brand colours.

Since `main`'s values now all live in `theme.css`, that sync got much cheaper: `stylized` overrides the
*values* (one phosphor hue) and inherits the *structure*. `report.css` should merge clean. Note that
`preview.py`'s live badge no longer carries its own green/amber/red - it reads `--ok`/`--warn`/`--bad`, so
it stops violating the one-hue rule on that branch for free.
