#!/usr/bin/env python3
"""Facts-only exporter for the report front end.

Reads `results/` + `tasks/tasks.json` and writes the static JSON the Vite/Preact pages consume, plus the
run screenshots. The output contract is `web/src/shared/contract.ts` — field names and shapes here are
that file, not this file's own invention.

    python3 runner/report_data.py --results results --out public

Emits, under --out:

    data/v1/run-index.json                       RunIndex: catalog + RunCells over model x tool x task
    data/v1/runs/<model>__<tool>__<task>.json    RunDetail, one per run that exists on disk
    data/v1/transcripts/<key>.json               RunTranscript, only where the run recorded events
    data/v1/report-meta.json                     ReportMeta
    data/v1/inventory.json                       {"paths": [...]}, every path this export owns
    artifacts/<model>__<tool>__<task>.webp        final screenshots (naming as docs/reporting.md)

`<tool>` above is a tool VARIANT — `argent@0.15.0`, one version of one toolkit — so a benchmark run
against a second version of the same toolkit lands beside the first instead of on top of it. See the
"tool variants" block below and docs/tool-versions.md.

NO AGGREGATES. Every number the report renders is derived by deriveReportView() in TypeScript; a second
aggregation implementation living here is the exact thing this migration exists to delete.

Four invariants this file is structured around:

1. The public gate runs before anything is written. `gather()` is the only constructor of a Dataset and
   it applies REPORT_APPS + shown_models + REPORT_MODELS itself; `export()` accepts nothing but a
   Dataset. A model that does not survive the gate has no route to a byte of output — no id, no cell, no
   file, no screenshot. `_assert_gated` re-checks the emitted path set, because a leaked screenshot is a
   path rather than a content match.
2. Only whitelisted meta/score keys are ever read (META_FIELDS / SCORE_FIELDS / JUDGE_INPUT_FIELDS). The
   raw dicts are projected through the whitelist at load time, so `stderr_tail` and `judge_prompt` are
   not merely unused downstream — they are not in memory. `env.json` is never opened at all.
3. Redaction is applied by the single writer, to every resource, rather than per call site.
4. Both exporter-owned trees are pruned to the computed path set, so a run that stops being eligible
   loses its JSON *and* its screenshot instead of staying deployed forever.

No `<script>` escaping happens here: nothing this exporter writes becomes markup. That obligation lives
in serializeInlineJson() on the Node side, at the one boundary where a payload is pasted into HTML.

Branch note: this module must stay byte-identical between the public and internal branches, exactly as
report.py does. Only the `bench_models` import and the BENCH_PUBLIC env var may differ. No
branch-conditional code.
"""
import argparse
import datetime
import json
import os
import re
from typing import NamedTuple

from PIL import Image

from bench_models import MODELS, LABELS, INTERNAL_MODELS   # roster (branch-specific)
from ledger import variant_id, split_variant, run_tool_version   # the on-disk layout names these
import editions as editions_config                               # which runs may be compared with which
try:
    from bench import HARNESS as CURRENT_HARNESS           # the current faithful harness stamp
except Exception:
    CURRENT_HARNESS = "isolation-v5"                       # keep in sync with bench.HARNESS
try:
    from judge import JUDGE_MODEL                          # fallback only: the judge recorded on disk wins
except Exception:
    JUDGE_MODEL = ""
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 4

# ---- roster helpers (lifted from report.py:49-97, semantics unchanged) ---------------------------
PUBLIC = os.environ.get("BENCH_PUBLIC") == "1"    # public view: hide internal models entirely
TOOLKITS = ["argent", "agent-device", "mobile-mcp", "none"]
TLABEL = {"none": "no tool"}                      # display label for a tool key (else the key itself)
BASELINE_TOOL = "none"                            # the no-tool baseline: "does the tool help at all?"
SCORE = {"success": 1.0, "partial": 0.5, "fail": 0.0, "error": 0.0, None: 0.0}
# "error" = the JUDGE failed to return a verdict (API flake), not the agent failing the task, so it is
# excluded from the grade denominator exactly like an unjudged run.
GRADED = ("success", "partial", "fail")
EFF_RANK = {"no think": 0, "no-think": 0, "none": 0, "low": 1, "med": 2, "medium": 2, "high": 3, "xhigh": 4}


def mlabel(m):
    return LABELS.get(m, m)


def is_internal(m):
    return m in INTERNAL_MODELS


def tl_label(tl):
    return TLABEL.get(tl, tl)


# label-level split: "gpt-5.4-mini (high)" -> base "gpt-5.4-mini" + thinking level "high" ("" if absent)
def base_of(lab):
    return str(lab).split(" (")[0].strip()


def think_of(lab):
    s = str(lab)
    return s[s.find("(") + 1:s.rfind(")")].strip() if "(" in s and ")" in s else ""


def eff_rank(lab):
    return EFF_RANK.get(think_of(lab).lower(), 99)   # order of a model's effort ladder


def base_name(m):
    return base_of(mlabel(m))                        # family name w/o the "(thinking)" suffix


def think_level(m):
    return think_of(mlabel(m))                       # just the thinking level, e.g. "low"/"med"


def provider_of(m):                                  # map a model to a provider key by its label
    s = mlabel(m).lower()
    if s.startswith(("gpt", "o1", "o3", "o4")) or "openai" in s:        return "openai"
    if any(t in s for t in ("claude", "haiku", "opus", "sonnet")):      return "anthropic"
    if any(t in s for t in ("gemma", "gemini", "palm")):                return "google"
    if "llama" in s or "meta" in s:                                     return "meta"
    if "grok" in s or "xai" in s:                                       return "xai"
    if "mistral" in s or "mixtral" in s:                                return "mistral"
    if "deepseek" in s:                                                 return "deepseek"
    if "qwen" in s:                                                     return "qwen"
    return None


def _load_versions(fname):   # small versions manifest under configs/ (drops _comment keys)
    try:
        with open(os.path.join(ROOT, "configs", fname)) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except Exception:
        return {}


APP_VERSIONS = _load_versions("app-versions.json")     # {app: {version, commit, repo}} — target surfaces


# ---- tool variants ------------------------------------------------------------------------------
# A *toolkit* is what the benchmark compares (argent, agent-device, the no-tool control). A *variant*
# is one version of one toolkit, and the variant id — `argent@0.15.0`, or the bare toolkit for a run
# that records no version — is what every id downstream is keyed on: a cell, a run key, a screenshot
# filename, a matrix column. So a second version of a toolkit ADDS a column instead of redefining the
# one that is there, and the reader never has to guess which build produced a number.
#
# The version comes off each run's own meta.json, never off the manifest, so a result claims only the
# build it was actually produced with. docs/tool-versions.md is the on-disk rule that keeps two
# versions of one toolkit from landing in the same result directory.
def _tool_manifest():
    """configs/tool-versions.json, normalised to {toolkit: {"pinned": v|None, "default": v|None}}.

    A bare string is shorthand for `{"pinned": <string>}`. `default` names the variant the site opens
    on when the newest one is not the one to lead with — a version part-way through a run, say, whose
    column is still mostly empty."""
    out = {}
    for toolkit, value in _load_versions("tool-versions.json").items():
        entry = {"pinned": value, "default": None} if isinstance(value, str) else value
        out[toolkit] = {"pinned": entry.get("pinned"), "default": entry.get("default")}
    return out


TOOL_MANIFEST = _tool_manifest()


def _version_key(version):
    """Newest-first ordering. Versions are dotted numbers (docs/tool-versions.md); anything else is
    ordered by whatever digits it carries, which is still stable and still puts 0.9 under 0.15."""
    return tuple(int(part) for part in re.findall(r"\d+", version or "")) or (0,)


# ---- output layout ------------------------------------------------------------------------------
# Root-absolute hrefs: both pages live at the site root ("/" and "/index-runs"), so a root-absolute
# path resolves identically from either, with or without cleanUrls rewriting.
DATA_DIR = "data/v1"
ARTIFACT_DIR = "artifacts"
DATA_ROOT = "/" + DATA_DIR
ARTIFACT_ROOT = "/" + ARTIFACT_DIR
INVENTORY_REL = DATA_DIR + "/inventory.json"
TELEMETRY_REL = DATA_DIR + "/telemetry.json"
EDITIONS_REL = DATA_DIR + "/editions.json"


def data_dir(ds):
    """Where a dataset's resources go. An edition's dataset is a self-contained copy of the layout
    under its own directory, so the site shows an edition by loading that directory and nothing else."""
    return f"{DATA_DIR}/editions/{ds.edition['id']}" if ds.edition else DATA_DIR


def artifact_dir(ds):
    return f"{ARTIFACT_DIR}/{ds.edition['id']}" if ds.edition else ARTIFACT_DIR

# ---- the telemetry gate -------------------------------------------------------------------------
# Telemetry ships to the internal site and nowhere else, and publish.yaml keeps that promise the way it
# keeps every other one: by deciding what is in the tree. `web/index-telemetry.html` is on the public
# environment's `paths: exclude` list, so a public export finds no page and writes no telemetry.json.
# Keying off the tree rather than an env var is also what leaves this module byte-identical between
# branches, as its own branch note requires. vite.config.ts and scripts/prerender.mts gate the page on
# the same file; ci/verify-release-tree.sh fails a tree that disagrees with its environment.
TELEMETRY_PAGE = os.path.join("web", "index-telemetry.html")


def telemetry_enabled(root):
    return os.path.exists(os.path.join(root, TELEMETRY_PAGE))


TELEMETRY = telemetry_enabled(ROOT)
# The two trees this exporter owns outright and therefore prunes. `assets` and `.vite` belong to Vite
# and are cleared by the build script; nothing else under --out is ours to delete.
PRUNE_ROOTS = ("data", ARTIFACT_DIR)

# ---- field whitelist ----------------------------------------------------------------------------
# The ONLY keys this exporter reads out of a run's meta.json / score.json. Deliberately absent, and
# never to be added: meta's `stderr_tail`, score's `judge_prompt` (~2.3 KB of rubric boilerplate per
# run whose every task-specific line is already a field here), and the run's `env.json`, which this
# module never opens. Raw dicts are projected through these sets at load time, so the excluded keys do
# not survive into any structure an emitter can reach.
META_FIELDS = frozenset({"model", "tool", "task", "app", "kind", "returncode", "timed_out",
                         "wall_s", "n_tool_calls", "tool_names", "versions"})
SCORE_FIELDS = frozenset({"verdict", "confidence", "reason", "judge_model", "judge_input"})
JUDGE_INPUT_FIELDS = frozenset({"app", "prompt", "solved_screen", "tool_names", "n_tool_calls"})


def _pick(d, allowed):
    return {k: v for k, v in d.items() if k in allowed}


# ---- redaction ----------------------------------------------------------------------------------
# The repo root leaks into every tool call's cwd/path. Replaced first, because it is a longer match
# that starts with a home dir.
_REDACTIONS = ((ROOT, "<repo>"),)
# Home dirs go by pattern, not by literal. A transcript carries the paths of the host that PRODUCED
# the run - overwhelmingly the bench host, not whatever machine builds the export - so
# `expanduser("~")` is the wrong needle for all but the handful exported where they were run. macOS
# only: every host that can drive a simulator is a Mac.
_HOME_RE = re.compile(r"/Users/[^/\"\\\s]+")


def redact(text):
    for needle, replacement in _REDACTIONS:
        text = text.replace(needle, replacement)
    return _HOME_RE.sub("~", text)


# ---- reading ------------------------------------------------------------------------------------
def load_tasks():
    d = json.load(open(os.path.join(ROOT, "tasks", "tasks.json")))
    return d["tasks"], {t["id"]: t for t in d["tasks"]}


# A tool call the model invented: opencode resolves the name against the offered schema, fails, and
# records the attempt under the tool id `invalid` with status "completed" - completed being the truth
# about the *handler*, which ran fine and returned an error message. So an invented call is invisible
# to any status-based error rate, which is why it is counted as its own kind here.
UNAVAILABLE_TOOL = "unavailable tool"


def _call_telemetry(part):
    """One tool call's telemetry: (name, duration ms or None, output bytes, kind).

    `duration` is opencode's `state.time`, and what it measures depends on how the toolkit reaches the
    device. For an MCP tool (`argent_*`, `agent-device_*`) it brackets the real call. For `bash` - how
    agent-device 0.17.6+ and the no-tool baseline drive everything - it brackets bookkeeping *after*
    the command returned, and medians 3-4 ms against commands that took seconds. Both are recorded
    verbatim; classifying them is `toolClock()` in web/src/shared/telemetry.ts, because which tools are
    MCP is a fact about the toolkit rather than about this run."""
    st = part.get("state", {}) or {}
    tm = st.get("time") or {}
    start, end = tm.get("start"), tm.get("end")
    inp = st.get("input")
    name = part.get("tool") or ""
    if name == "invalid" or (isinstance(inp, dict) and UNAVAILABLE_TOOL in str(inp.get("error") or "")):
        kind = "unavailable"
    elif (st.get("metadata") or {}).get("interrupted"):
        kind = "aborted"          # the run was killed mid-call; the call never got to succeed or fail
    elif st.get("status") == "error":
        kind = "error"
    else:
        kind = "ok"
    duration = (end - start) if (start and end) else None
    return name, duration, len(str(st.get("output") or "")), kind


def parse_chat(path):
    """Compact chat from a run's transcript.jsonl: assistant text, reasoning, and tool calls (name /
    input / truncated output / status). Base64 screenshot attachments are dropped (huge). Also returns
    the run's total cost in USD (sum of step_finish costs; 0.0 for local/free models, None if the run
    produced no transcript / no cost fields at all), and - only when the telemetry gate is open - the
    per-call and per-step facts the telemetry screen aggregates.

    The telemetry pass rides along here rather than in a second reader because this loop already
    json-decodes every line of a 5 GB corpus; the marginal cost is a handful of dict lookups."""
    out = []
    cost = None
    calls = []          # [(name, duration ms, output bytes, kind)]
    steps = []          # [(wall ms, input, output, reasoning, cache read, cache write)]
    step_start = None
    try:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            typ = ev.get("type"); part = ev.get("part", {}) or {}
            if typ == "text":
                x = (part.get("text") or "").strip()
                if x:
                    out.append({"k": "t", "x": x, "ts": ev.get("timestamp")})
            elif typ == "reasoning":
                x = (part.get("text") or "").strip()
                if x:
                    out.append({"k": "r", "x": x, "ts": ev.get("timestamp")})
            elif typ == "tool_use":
                st = part.get("state", {}) or {}
                out.append({"k": "u", "n": part.get("tool") or "", "i": st.get("input") or {},
                            "o": str(st.get("output") or "")[:320], "s": st.get("status") or "",
                            "ts": ev.get("timestamp")})
                if TELEMETRY:
                    calls.append(_call_telemetry(part))
            elif typ == "step_start":
                step_start = ev.get("timestamp")
            elif typ == "step_finish":
                c = part.get("cost")
                if c is not None:
                    cost = (cost or 0.0) + c
                if TELEMETRY:
                    tk = part.get("tokens") or {}
                    ca = tk.get("cache") or {}
                    stamp = ev.get("timestamp")
                    wall = (stamp - step_start) if (stamp and step_start) else None
                    steps.append((wall, tk.get("input") or 0, tk.get("output") or 0,
                                  tk.get("reasoning") or 0, ca.get("read") or 0, ca.get("write") or 0))
                    step_start = None
    except FileNotFoundError:
        pass
    return out, cost, ((calls, steps) if TELEMETRY else None)


def collect(results_dir):
    """{(model, tool variant, task): row} over every run on disk. meta/score are projected through the
    field whitelist here, which is the only place either file is opened.

    The key's tool component is the VARIANT id, read out of the run's own version stamp — so two
    versions of one toolkit are two rows, not one overwriting the other."""
    rows = {}
    if not os.path.isdir(results_dir):
        return rows
    for cell in os.listdir(results_dir):
        cdir = os.path.join(results_dir, cell)
        if not os.path.isdir(cdir):
            continue
        for tid in os.listdir(cdir):
            rdir = os.path.join(cdir, tid)
            mp = os.path.join(rdir, "meta.json")
            if not os.path.exists(mp):
                continue
            meta = _pick(json.load(open(mp)), META_FIELDS)
            sp = os.path.join(rdir, "score.json")
            raw_score = json.load(open(sp)) if (os.path.exists(sp) and os.path.getsize(sp)) else {}
            score = _pick(raw_score, SCORE_FIELDS)
            png = os.path.join(rdir, "final.png")
            chat, cost, tele = parse_chat(os.path.join(rdir, "transcript.jsonl"))
            harness = (meta.get("versions") or {}).get("harness")
            polluted = harness != CURRENT_HARNESS
            ji = score.get("judge_input")   # judge telemetry: EXACTLY what the judge was shown. Absent
            ji = _pick(ji, JUDGE_INPUT_FIELDS) if isinstance(ji, dict) else None   # on every score.json
            tool_id = variant_id(meta["tool"], run_tool_version(meta))
            rows[(meta["model"], tool_id, tid)] = {        # written before judge.py started recording it
                "meta": meta, "verdict": score.get("verdict"),
                "judge": score.get("judge_model"),   # the model that actually scored THIS run
                "reason": score.get("reason", ""), "conf": score.get("confidence"),
                "judge_input": ji,
                "png": png if os.path.exists(png) else None,
                "chat": chat, "cost": cost, "tele": tele,
                "harness": harness,
                # A result produced by another harness than the one it is being read against: shown,
                # but never scored. edition_rows() re-reads this against the run's own edition.
                "polluted": polluted}
    return rows


def edition_rows(root=None, cfg=None):
    """{edition id: rows}, every run on disk under exactly one edition (configs/editions.json).

    A results directory one edition owns holds that edition's runs. The bare `results/` tree is shared
    by the editions that predate the layout, and there a run goes to the edition naming its harness
    stamp, else to the directory's catch-all. `polluted` is then relative to the run's OWN edition: a
    run is stale only if it does not carry its edition's harness, which is every run of a catch-all."""
    out = {e["id"]: {} for e in editions_config.all_editions(cfg)}
    for directory, sharing in editions_config.by_directory(cfg, root).items():
        for key, row in collect(directory).items():
            edition = editions_config.of(row["harness"], sharing) or sharing[0]
            row["polluted"] = not edition.get("harness") or row["harness"] != edition["harness"]
            out[edition["id"]][key] = row
    return out


def shown_models(rows, roster=None):
    """Models to render: internal ones are dropped in public mode, and also whenever the dataset carries
    no result rows for them — so the public branch (which ships no internal dirs) never surfaces them,
    flag or not. Non-internal models always show (pending ones render as empty columns).

    With `roster` (an edition's): only the models that edition is made of — those it names, which show
    while still pending, and those it has runs for. A model another edition ran is not a pending
    column here; it was never part of this comparison."""
    have = {k[0] for k in rows}

    def ok(m):
        if roster is not None and m not in roster and m not in have:
            return False
        if not is_internal(m):
            return True
        if PUBLIC:
            return False
        return m in have
    return [m for m in MODELS if ok(m)]


def tool_variants(rows, pending=True):
    """Every (toolkit, version) the dataset carries, in TOOLKITS order and newest version first.

    A toolkit with no runs still gets one variant — its pin, or its bare id — because a column with
    nothing in it yet is a pending column, not an absent tool. Not so for a past edition (`pending`
    false): nothing more will be recorded there, so a toolkit it never ran is simply absent."""
    versions = {toolkit: set() for toolkit in TOOLKITS}
    for _model, tool_id, _task in rows:
        toolkit, version = split_variant(tool_id)
        if toolkit in versions:
            versions[toolkit].add(version)
    out = []
    for toolkit in TOOLKITS:
        found = versions[toolkit] or ({TOOL_MANIFEST.get(toolkit, {}).get("pinned")} if pending else set())
        out.extend((toolkit, v) for v in sorted((v for v in found if v), key=_version_key, reverse=True))
        if None in found:
            out.append((toolkit, None))   # version never recorded: ordered under every version that was
    return out


def latest_versions(variants):
    """{toolkit: newest recorded version}. None for a toolkit whose runs carry no version at all."""
    return {toolkit: next((v for t, v in variants if t == toolkit and v), None) for toolkit in TOOLKITS}


def default_tool_ids(variants, manifest=True):
    """The one variant per toolkit the site opens on: the version `configs/tool-versions.json` names
    as `default`, else the newest one recorded. Every other variant is carried but hidden until the
    reader picks it. `default` speaks about the edition being recorded, so a past edition (`manifest`
    false) always opens on its newest."""
    out = []
    for toolkit in TOOLKITS:
        mine = [v for t, v in variants if t == toolkit]
        if not mine:
            continue
        # `wanted and` is load-bearing: an unversioned variant is None, and a toolkit with no `default`
        # declared also reads None, so a bare `wanted in mine` hands the default to the version-less
        # legacy runs instead of to the newest build.
        wanted = TOOL_MANIFEST.get(toolkit, {}).get("default") if manifest else None
        out.append(variant_id(toolkit, wanted if (wanted and wanted in mine) else mine[0]))
    return out


def unit_state(rows, m, tl, tid):
    """DAG state per unit, derived from disk: pending -> completed (ran, awaiting judge) -> judged."""
    r = rows.get((m, tl, tid))
    if not r or r["meta"].get("returncode") == -2:
        return "pending"
    if r.get("polluted"):     # result from an older/polluted harness — designated for rerun
        return "stale"
    if r["verdict"] in GRADED:
        return "judged"
    if r["png"]:
        return "completed"
    return "pending"


# ---- the gate -----------------------------------------------------------------------------------
class Dataset(NamedTuple):
    """Everything downstream of the public gate. The only constructor is `gather()`, and `export()`
    takes nothing else, so an ineligible model cannot reach an emitter by any path."""
    tasks: list
    tmap: dict
    models: list
    rows: dict
    annulled: frozenset
    variants: list      # [(toolkit, version)], TOOLKITS order, newest version first
    defaults: list      # variant ids the site opens on, one per toolkit
    edition: dict = None    # the edition this dataset is (configs/editions.json); None = a bare tree


def gather(results_dir=None, edition=None, rows=None, current=True):
    """Read the dataset and apply every eligibility rule, in report.py's order. Nothing is written.

    For an edition, pass its `rows` (edition_rows) and whether it is the `current` one: a past
    edition shows only the models and tool versions it has runs for."""
    tasks, tmap = load_tasks()
    # Optional app filter (env): REPORT_APPS=element restricts the whole export to those apps.
    apps_env = os.environ.get("REPORT_APPS")
    if apps_env:
        keep_apps = set(apps_env.split(","))
        tasks = [t for t in tasks if t["app"] in keep_apps]
        tmap = {t["id"]: t for t in tasks}
    rows = collect(results_dir) if rows is None else rows
    # Orphan result dirs (a task id no longer in tasks.json) have no cell and no catalog entry, so they
    # would be unreachable files. Drop them here rather than emitting dead weight.
    rows = {k: v for k, v in rows.items() if k[2] in tmap}
    models = shown_models(rows, None if edition is None else (edition.get("models") or []) if current else [])
    # Optional model filter (env): REPORT_MODELS=gpt_low,gpt_high restricts the export to exactly those.
    # Carried over verbatim from report.py:2486-2489, `or keep` fallback included, because this migration
    # is parity-only.
    #
    # KNOWN GATE HOLE, deliberately preserved: shown_models() only ever drops internal models, so the
    # intersection below is empty exactly when every requested id is internal-and-hidden — and `or keep`
    # then re-admits them. `BENCH_PUBLIC=1 REPORT_MODELS=<internal-id>` therefore publishes an internal
    # model. Harmless on this branch (INTERNAL_MODELS is empty, so the intersection is never empty for a
    # valid id), live on the internal branch. Fixing it means deleting `or keep`, which makes the filter
    # strictly narrowing; that is a behaviour change and belongs in its own commit, not this one.
    mods_env = os.environ.get("REPORT_MODELS")
    if mods_env:
        keep = [m for m in mods_env.split(",") if m in MODELS]
        # An edition narrows strictly: `or keep` would hand a past edition pending columns for models
        # it never ran.
        models = [m for m in keep if m in models] or ([] if edition else keep)   # preserve requested order
    rows = {k: v for k, v in rows.items() if k[0] in set(models)}
    variants = tool_variants(rows, pending=current)
    return Dataset(tasks=tasks, tmap=tmap, models=models, rows=rows,
                   annulled=frozenset(t["id"] for t in tasks if t.get("annulled")),
                   variants=variants, defaults=default_tool_ids(variants, manifest=current),
                   edition=edition)


def gather_editions(root=None, cfg=None):
    """One Dataset per edition that has something to show, newest first. An edition with no run that
    survived the gates is left out, except that the current edition stands in when none has any — a
    fresh tree still builds a site."""
    cfg = cfg or editions_config.load()
    rows = edition_rows(root, cfg)
    now = editions_config.current(cfg)["id"]
    out = [gather(edition=e, rows=rows[e["id"]], current=e["id"] == now)
           for e in editions_config.all_editions(cfg)]
    return [ds for ds in out if ds.rows] or [ds for ds in out if ds.edition["id"] == now]


def edition_entry(ds, cfg=None):
    """What the site knows about an edition: enough to label it and say what it froze."""
    e = ds.edition
    return {"id": e["id"], "label": e["label"],
            "current": e["id"] == editions_config.current(cfg)["id"],
            # An edition with no harness of its own is a catch-all for runs that predate the stamp:
            # browsable, never scored.
            "ranked": bool(e.get("harness")),
            "harness": e.get("harness"), "opencode": e.get("opencode"), "judge": e.get("judge"),
            "runs": len(ds.rows)}


def default_edition_id(datasets):
    """The edition the site opens on: the newest with a scored run, so an edition that has only just
    started recording does not take the front page with an empty table."""
    scored = lambda ds: any(not r["polluted"] and r["verdict"] in GRADED for r in ds.rows.values())
    return next((ds for ds in datasets if scored(ds)), datasets[0]).edition["id"]


# ---- resource construction ----------------------------------------------------------------------
def run_key(m, tl, tid):
    return f"{m}__{tl}__{tid}"


def _stamp(value):
    """The explorer's timeNumber(): a number passes through, anything else is parsed as a date, and an
    unparseable value is 0 (and therefore ignored as a baseline candidate)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp() * 1000
    except Exception:
        return 0


def step_label(ev):
    """RUN_EXPLORER_JS stepLabel(): the tool call's own description, else the first line of its command,
    else the tool name."""
    inp = ev.get("i")
    raw = ""
    if isinstance(inp, dict):
        description = inp.get("description")
        if isinstance(description, str):
            raw = description.strip()
        if not raw:
            command = inp.get("command")
            if isinstance(command, str):
                raw = command.strip().split("\n")[0]
    return raw or ev.get("n") or "tool call"


def steps_of(chat):
    """RunDetail.steps — one entry per tool_use event, elapsed from the run's first stamped event.

    `index` is the 1-based step number the run detail prints, not the array position: the array already
    carries the position, so a 0-based copy of it would add nothing."""
    stamps = sorted(s for s in (_stamp(e.get("ts")) for e in chat) if s)
    base = stamps[0] if stamps else 0
    steps = []
    for i, ev in enumerate([e for e in chat if e.get("k") == "u"], 1):
        stamp = _stamp(ev.get("ts"))
        elapsed = max(0, round((stamp - base) / 1000)) if (base and stamp) else None
        steps.append({"index": i, "name": ev.get("n") or "", "label": step_label(ev),
                      "elapsedSeconds": elapsed, "status": ev.get("s") or None})
    return steps


def judge_input_of(row, task):
    """RunDetail.judgeInput. `recorded` is judge.py's own verbatim record of what the judge was shown;
    `reconstructed` rebuilds the same facts from tasks.json + the run's meta.json for the runs scored
    before judge.py started writing it (the fallback chain the current run detail applies client-side)."""
    ji = row["judge_input"]
    if ji is not None:
        return {"source": "recorded",
                "app": ji.get("app") or "",
                "prompt": ji.get("prompt") or "",
                "solvedScreen": ji.get("solved_screen") or "",
                "toolNames": list(ji.get("tool_names") or []),
                "toolCallCount": int(ji.get("n_tool_calls") or 0)}
    meta = row["meta"]
    return {"source": "reconstructed",
            "app": task.get("app") or meta.get("app") or "",
            "prompt": task.get("prompt") or "",
            "solvedScreen": task.get("solved_screen") or "",
            "toolNames": list(meta.get("tool_names") or []),
            "toolCallCount": int(meta.get("n_tool_calls") or 0)}


def model_entry(m):
    return {"id": m, "label": mlabel(m), "provider": provider_of(m), "base": base_name(m),
            "effort": think_level(m) or None, "effortRank": eff_rank(mlabel(m))}


def tool_entries(ds):
    latest = latest_versions(ds.variants)
    return [{"id": variant_id(toolkit, version), "toolkit": toolkit, "label": tl_label(toolkit),
             "version": version, "isLatest": version == latest[toolkit]}
            for toolkit, version in ds.variants]


def build_cells(ds):
    """One cell per (model, tool variant, task) — except that a variant the site does not open on
    emits only the runs that exist. The full cross-product there would add a pending cell for every
    task nobody ever ran at that version, growing the index by a whole matrix per version to say
    nothing; a missing cell already reads as pending everywhere it is consumed."""
    defaults = set(ds.defaults)
    cells = []
    for m in ds.models:
        for toolkit, version in ds.variants:
            tool_id = variant_id(toolkit, version)
            for t in ds.tasks:
                row = ds.rows.get((m, tool_id, t["id"]))
                if row is None and tool_id not in defaults:
                    continue
                cells.append({"modelId": m, "toolId": tool_id, "taskId": t["id"],
                              "lifecycle": unit_state(ds.rows, m, tool_id, t["id"]),
                              "verdict": (row or {}).get("verdict") or None,
                              "wallSeconds": ((row or {}).get("meta") or {}).get("wall_s"),
                              "costUsd": (row or {}).get("cost")})
    return cells


def build_run_index(ds):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "scoring": {"weights": {v: SCORE[v] for v in GRADED},
                    "gradedVerdicts": list(GRADED),
                    "baselineToolId": BASELINE_TOOL},
        "catalog": {
            "models": [model_entry(m) for m in ds.models],
            "tools": tool_entries(ds),
            # What a reader sees before touching a control; the rest of `tools` is carried but hidden.
            "defaultToolIds": list(ds.defaults),
            "tasks": [{"id": t["id"], "app": t.get("app") or "", "kind": t.get("kind") or "",
                       "prompt": t.get("prompt") or "", "annulled": bool(t.get("annulled"))}
                      for t in ds.tasks],
        },
        "cells": build_cells(ds),
    }


def build_run_detail(ds, m, tl, tid):
    row = ds.rows[(m, tl, tid)]
    meta = row["meta"]
    key = run_key(m, tl, tid)
    events = row["chat"]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "run": {
            "lifecycle": unit_state(ds.rows, m, tl, tid),
            "verdict": row["verdict"] or None,
            "confidence": row["conf"],
            "reason": row["reason"] or "",
            "judgeModel": row["judge"] or None,
            "wallSeconds": meta.get("wall_s"),
            "costUsd": row["cost"],
            "timedOut": bool(meta.get("timed_out")),
            "toolCallCount": int(meta.get("n_tool_calls") or 0),
            "toolNames": list(meta.get("tool_names") or []),
            "screenshotHref": f"/{artifact_dir(ds)}/{key}.webp" if row["png"] else None,
        },
        "judgeInput": judge_input_of(row, ds.tmap.get(tid) or {}),
        "steps": steps_of(events),
        # No events -> no file and href null, so "No transcript recorded" never costs a 404.
        "transcript": {"href": f"/{data_dir(ds)}/transcripts/{key}.json" if events else None,
                       "eventCount": len(events)},
    }


# Three real runs anchor the methodology explanation: one success, one partial, one failure. They use
# the same rows/tasks as the rest of the export, so the examples cannot drift from published evidence.
# The model is a preference, not a requirement: an edition that never ran gpt_high takes the example
# from the first of its own models that has a usable run of the same toolkit and task, so each
# edition's methodology panel shows that edition's evidence. A dataset with no such run emits none.
# The tool is named as a TOOLKIT: which version supplies the example is resolved against the data
# below, so bumping a version does not silently empty the methodology panel.
METHOD_SPECS = [
    (("gpt_high", "agent-device", "element-28"), "Compose", "Send “status update”"),
    (("gpt_high", "argent", "element-22"), "Search", "Search for “update”"),
    (("gpt_high", "none", "bsky-25"), "Reply", "Reply “nice one”"),
]


def _usable(row):
    return bool(row) and bool(row["png"]) and row["verdict"] in GRADED


def example_variant(ds, model, toolkit, tid):
    """The variant whose run anchors a methodology example: the toolkit's default version if that run
    is usable, else its newest usable one. Returns None when no version of the toolkit ran the task."""
    ordered = [variant_id(t, v) for t, v in ds.variants if t == toolkit]
    ordered.sort(key=lambda tool_id: tool_id not in ds.defaults)   # default first, then newest-first
    return next((tool_id for tool_id in ordered if _usable(ds.rows.get((model, tool_id, tid)))), None)


def build_method_examples(ds):
    examples = []
    for (preferred, toolkit, tid), action, title in METHOD_SPECS:
        task = ds.tmap.get(tid)
        candidates = [preferred] + [m for m in reversed(ds.models) if m != preferred]
        m, tl = next(((m, tl) for m in candidates
                      for tl in [example_variant(ds, m, toolkit, tid)] if tl), (None, None))
        if not tl or not task:
            continue
        row = ds.rows[(m, tl, tid)]
        meta = row["meta"]
        examples.append({
            "app": task.get("app") or "", "kind": task.get("kind") or "",
            "title": title, "action": action, "taskId": tid,
            # The methodology panel names the configuration that produced the example and shows the
            # judge's own confidence beside the verdict. Ids, not labels: the labels are already in
            # ReportInitial.models/tools, and a resource carries stable ids and raw values.
            "modelId": m, "toolId": tl, "confidence": row["conf"],
            "prompt": task.get("prompt") or "",
            "solvedScreen": task.get("solved_screen") or "",
            "verdict": row["verdict"], "reason": row["reason"] or "",
            "wallSeconds": meta.get("wall_s"),
            "toolCallCount": int(meta.get("n_tool_calls") or 0),
            "screenshotHref": f"/{artifact_dir(ds)}/{run_key(m, tl, tid)}.webp",
        })
    return examples


def build_provenance(ds, now):
    # The judge, read off the verdicts on disk rather than hardcoded, so it cannot go stale when
    # judge.py is repointed. It covers every model in this export.
    judges = sorted({row["judge"] for k, row in ds.rows.items()
                     if row["judge"] and row["verdict"] and k[2] not in ds.annulled})
    judge_str = " / ".join(judges) or ((ds.edition or {}).get("judge") or JUDGE_MODEL)
    apps = sorted({t["app"] for t in ds.tasks})
    # Every version of every toolkit this export carries, newest first — not the manifest's pin, which
    # says what the next run should use rather than what these results were produced with.
    tool_versions = {toolkit: [v for t, v in ds.variants if t == toolkit and v] for toolkit in TOOLKITS}
    out = {} if not ds.edition else {"editionLine": " · ".join(
        [f"edition {ds.edition['label']}"]
        + ([f"harness {ds.edition['harness']}"] if ds.edition.get("harness") else ["unranked: runs from before harness stamps"])
        + ([f"OpenCode {ds.edition['opencode']}"] if ds.edition.get("opencode") else []))}
    return {
        **out,
        "generatedAt": now,
        "judgeLine": f"judge {judge_str} vision · iOS Simulator" if judge_str else "iOS Simulator",
        "toolVersions": {t: vs for t, vs in tool_versions.items() if vs},
        # only the surfaces THIS export covers — the manifest also pins apps that are frozen but not run
        "appVersions": {a: APP_VERSIONS[a] for a in apps
                        if a in APP_VERSIONS
                        and (APP_VERSIONS[a].get("version") or APP_VERSIONS[a].get("commit"))},
    }


def build_report_meta(ds, now, build_id, catalog=None):
    """`catalog` is the export's edition list ({"editions": [...], "defaultEditionId": ...}); it rides
    in the manifest, which every page embeds, so each page can offer the other editions."""
    manifest = {"schemaVersion": SCHEMA_VERSION, "buildId": build_id,
                "dataRoot": "/" + data_dir(ds), "artifactRoot": "/" + artifact_dir(ds)}
    if ds.edition:
        manifest.update(edition=edition_entry(ds), editions=(catalog or {}).get("editions") or [edition_entry(ds)],
                        defaultEditionId=(catalog or {}).get("defaultEditionId") or ds.edition["id"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "models": [model_entry(m) for m in ds.models],
        "tools": tool_entries(ds),
        "defaultToolIds": list(ds.defaults),
        "provenance": build_provenance(ds, now),
        "methodExamples": build_method_examples(ds),
        "manifest": manifest,
        # Deliberately no `view`: Node derives it from RunIndex, avoiding a second aggregation
        # implementation in Python.
    }


# The kinds a tool call can end in, indexed by `Telemetry.runs[].calls[][3]`. Order is the contract;
# append only.
CALL_KINDS = ("ok", "error", "aborted", "unavailable")


def build_telemetry(ds):
    """Telemetry: per-call and per-step facts for every run that recorded a transcript.

    NO AGGREGATES, as everywhere else here - the rollups the screen renders are derived by
    deriveTelemetryView() in web/src/shared/telemetry.ts. What this emits is one row per run, holding
    the calls and steps that run actually made.

    Calls are `[toolNameIndex, durationMs, outputBytes, kindIndex]` against the `toolNames` dictionary
    rather than repeated objects: at 41.7k calls the field names cost more than the values do, and the
    whole dataset is one fetch. `durationMs` is null when opencode stamped no `state.time`; see
    _call_telemetry for what it does and does not measure.

    `firstStepContext` is the billable context of the run's FIRST step - input + both cache halves,
    before any tool output has accumulated - i.e. what the toolkit's own surface costs to put in front
    of the model, charged again on every step after it."""
    names = {}
    def name_index(n):
        return names.setdefault(n, len(names))

    runs = []
    for (m, tl, tid), row in sorted(ds.rows.items()):
        tele = row.get("tele")
        if not tele:
            continue
        calls, steps = tele
        if not calls and not steps:
            continue
        totals = {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}
        for _wall, i, o, r, cr, cw in steps:
            totals["input"] += i; totals["output"] += o; totals["reasoning"] += r
            totals["cacheRead"] += cr; totals["cacheWrite"] += cw
        first = steps[0] if steps else None
        runs.append({
            "modelId": m, "toolId": tl, "taskId": tid,
            "wallSeconds": row["meta"].get("wall_s"),
            "costUsd": row["cost"],
            "firstStepContext": (first[1] + first[4] + first[5]) if first else None,
            "tokens": totals,
            "stepWallMs": [w for w, *_ in steps if w is not None],
            "calls": [[name_index(n), ms, b, CALL_KINDS.index(k)] for n, ms, b, k in calls],
        })
    return {"schemaVersion": SCHEMA_VERSION,
            "callKinds": list(CALL_KINDS),
            "toolNames": [n for n, _ in sorted(names.items(), key=lambda kv: kv[1])],
            "runs": runs}


def build_resources(ds, now, build_id, catalog=None):
    """(relpath -> JSON payload, [(source png, relpath)]) for one dataset, under its own directory.
    Pure: reads a Dataset, touches no disk."""
    base, shots = data_dir(ds), artifact_dir(ds)
    resources = {f"{base}/run-index.json": build_run_index(ds),
                 f"{base}/report-meta.json": build_report_meta(ds, now, build_id, catalog)}
    # Internal only, and by omission rather than by deletion: on a public tree TELEMETRY is false, this
    # key is never added, and prune() then removes any telemetry.json a previous build left behind.
    if TELEMETRY:
        resources[f"{base}/telemetry.json"] = build_telemetry(ds)
    screenshots = []
    for (m, tl, tid), row in sorted(ds.rows.items()):
        key = run_key(m, tl, tid)
        resources[f"{base}/runs/{key}.json"] = build_run_detail(ds, m, tl, tid)
        if row["chat"]:
            resources[f"{base}/transcripts/{key}.json"] = {"schemaVersion": SCHEMA_VERSION,
                                                           "events": row["chat"]}
        if row["png"]:
            screenshots.append((row["png"], f"{shots}/{key}.webp"))
    return resources, screenshots


def build_catalog(datasets):
    """data/v1/editions.json: the editions this export carries, newest first, and the one to open on."""
    return {"schemaVersion": SCHEMA_VERSION, "defaultEditionId": default_edition_id(datasets),
            "editions": [edition_entry(ds) for ds in datasets]}


# ---- writing ------------------------------------------------------------------------------------
def _assert_gated(paths, models):
    """A leaked screenshot shows up as a PATH, not as file content, so check the path set too: every
    emitted `<model>__<variant>__<task>` name must belong to a model that survived the gate."""
    allowed = set(models)
    for rel in paths:
        name = os.path.basename(rel)
        if "__" in name:
            owner = name.split("__", 1)[0]
            if owner not in allowed:
                raise SystemExit(f"gate violation: {rel} names non-exported model {owner!r}")


def prune(out_dir, owned):
    """Delete every file under the exporter-owned trees that this export does not own, then drop the
    directories that empties. report.py only ever copied artifacts in, which is why public/artifacts
    accumulated screenshots for models long gone from the roster. Pruning rather than rm -rf keeps the
    artifacts copy incremental locally; CI starts from an empty public/ and converges to the same set."""
    removed = 0
    for root_name in PRUNE_ROOTS:
        base = os.path.join(out_dir, root_name)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base, topdown=False):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, out_dir).replace(os.sep, "/")
                if rel not in owned:
                    os.remove(full)
                    removed += 1
            if dirpath != base and not os.listdir(dirpath):
                os.rmdir(dirpath)
    return removed


def write_json(out_dir, rel, payload):
    """The single writer. Redaction happens here so no emitter can forget it, and no <script> escaping
    happens here because nothing this exporter writes becomes markup."""
    path = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = redact(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def copy_screenshot(out_dir, src, rel):
    """Incremental: the artifacts tree is large, so an unchanged source PNG is left alone. Output is a
    re-encoded lossy WebP (~90% smaller than the source PNG), so dst size never matches src size — the
    incremental check compares src mtime against dst mtime instead, and os.utime stamps dst with src's
    mtime after encoding so that comparison stays meaningful on the next run."""
    dst = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    s = os.stat(src)
    try:
        d = os.stat(dst)
        if int(s.st_mtime) == int(d.st_mtime):
            return False
    except OSError:
        pass
    Image.open(src).save(dst, "WEBP", quality=82, method=6)
    os.utime(dst, (s.st_mtime, s.st_mtime))
    return True


def export(datasets, out_dir, now, build_id):
    """The only function that writes. It takes Datasets and nothing else, so the gate applied in
    gather() is the gate applied to every byte on disk. One dataset per edition, each under its own
    directory, plus the list of them."""
    datasets = [datasets] if isinstance(datasets, Dataset) else list(datasets)
    catalog = build_catalog(datasets) if all(ds.edition for ds in datasets) else None
    resources, screenshots, cells = {}, [], 0
    for ds in datasets:
        mine, shots = build_resources(ds, now, build_id, catalog)
        cells += len(mine[f"{data_dir(ds)}/run-index.json"]["cells"])
        resources.update(mine)
        screenshots += shots
    if catalog:
        resources[EDITIONS_REL] = catalog
    paths = sorted(set(resources) | {rel for _src, rel in screenshots} | {INVENTORY_REL})
    _assert_gated(paths, {m for ds in datasets for m in ds.models})
    resources[INVENTORY_REL] = {"paths": paths}

    removed = prune(out_dir, set(paths))
    for rel, payload in resources.items():
        write_json(out_dir, rel, payload)
    copied = sum(1 for src, rel in screenshots if copy_screenshot(out_dir, src, rel))
    return {"paths": len(paths), "resources": len(resources), "screenshots": len(screenshots),
            "copied": copied, "removed": removed, "cells": cells}


def main():
    ap = argparse.ArgumentParser(description="Export the report's static JSON + screenshots.")
    ap.add_argument("--results", default=os.path.join(ROOT, "results"),
                    help="the bare results tree; editions (configs/editions.json) resolve beside it")
    ap.add_argument("--out", default=os.path.join(ROOT, "public"))
    a = ap.parse_args()

    # Every eligibility rule, before a single byte is written. Edition directories are relative to the
    # checkout that holds `results/`.
    datasets = gather_editions(os.path.dirname(os.path.abspath(a.results)))
    now = datetime.datetime.now()
    stats = export(datasets, a.out, now.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y%m%dT%H%M%S"))

    print(f"export -> {a.out}")
    print(f"  {len(datasets)} edition(s), opening on {default_edition_id(datasets)}; {stats['cells']} cells")
    for ds in datasets:
        transcripts = sum(1 for r in ds.rows.values() if r["chat"])
        print(f"  {ds.edition['id']}: {len(ds.models)} models x {len(ds.variants)} tool versions x "
              f"{len(ds.tasks)} tasks ({len(ds.annulled)} annulled); {len(ds.rows)} runs, "
              f"{transcripts} transcripts; shown by default: {', '.join(ds.defaults)}")
    print(f"  {stats['screenshots']} screenshots ({stats['copied']} copied)")
    print(f"  {stats['paths']} paths owned, {stats['removed']} stale file(s) pruned")
    print(f"  telemetry: {'on' if TELEMETRY else f'off (no {TELEMETRY_PAGE})'}")


if __name__ == "__main__":
    main()
