#!/usr/bin/env python3
"""Facts-only exporter for the report front end.

Reads `results/` + `tasks/tasks.json` and writes the static JSON the Vite/Preact pages consume, plus the
run screenshots. The output contract is `web/src/shared/contract.ts` — field names and shapes here are
that file, not this file's own invention.

    python3 runner/report_data.py --results results --out public

Emits, under --out:

    data/v1/run-index.json                       RunIndex: catalog + one RunCell per model x tool x task
    data/v1/runs/<model>__<tool>__<task>.json    RunDetail, one per run that exists on disk
    data/v1/transcripts/<key>.json               RunTranscript, only where the run recorded events
    data/v1/report-meta.json                     ReportMeta
    data/v1/inventory.json                       {"paths": [...]}, every path this export owns
    artifacts/<model>__<tool>__<task>.webp        final screenshots (naming as docs/reporting.md)

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
from typing import NamedTuple

from PIL import Image

from bench_models import MODELS, LABELS, INTERNAL_MODELS   # roster (branch-specific)
try:
    from bench import HARNESS as CURRENT_HARNESS           # the current faithful harness stamp
except Exception:
    CURRENT_HARNESS = "isolation-v3-progressive"           # keep in sync with bench.HARNESS
try:
    from judge import JUDGE_MODEL                          # fallback only: the judge recorded on disk wins
except Exception:
    JUDGE_MODEL = ""
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_VERSION = 2

# ---- roster helpers (lifted from report.py:49-97, semantics unchanged) ---------------------------
PUBLIC = os.environ.get("BENCH_PUBLIC") == "1"    # public view: hide internal models entirely
TOOLS = ["argent", "agent-device", "none"]
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


TOOL_VERSIONS = _load_versions("tool-versions.json")   # {tool: version} — the agents driving the apps
APP_VERSIONS = _load_versions("app-versions.json")     # {app: {version, commit, repo}} — target surfaces

# ---- output layout ------------------------------------------------------------------------------
# Root-absolute hrefs: both pages live at the site root ("/" and "/index-runs"), so a root-absolute
# path resolves identically from either, with or without cleanUrls rewriting.
DATA_DIR = "data/v1"
ARTIFACT_DIR = "artifacts"
DATA_ROOT = "/" + DATA_DIR
ARTIFACT_ROOT = "/" + ARTIFACT_DIR
INVENTORY_REL = DATA_DIR + "/inventory.json"
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
# The existing literal scrub: the repo root (which carries the internal repo name on the internal
# branch) and the home dir leak into every tool call's cwd/path. ROOT is replaced first because it is
# a longer match that starts with the home dir.
_REDACTIONS = ((ROOT, "<repo>"), (os.path.expanduser("~"), "~"))


def redact(text):
    for needle, replacement in _REDACTIONS:
        text = text.replace(needle, replacement)
    return text


# ---- reading ------------------------------------------------------------------------------------
def load_tasks():
    d = json.load(open(os.path.join(ROOT, "tasks", "tasks.json")))
    return d["tasks"], {t["id"]: t for t in d["tasks"]}


def parse_chat(path):
    """Compact chat from a run's transcript.jsonl: assistant text, reasoning, and tool calls (name /
    input / truncated output / status). Base64 screenshot attachments are dropped (huge). Also returns
    the run's total cost in USD (sum of step_finish costs; 0.0 for local/free models, None if the run
    produced no transcript / no cost fields at all)."""
    out = []
    cost = None
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
            elif typ == "step_finish":
                c = part.get("cost")
                if c is not None:
                    cost = (cost or 0.0) + c
    except FileNotFoundError:
        pass
    return out, cost


def collect(results_dir):
    """{(model, tool, task): row} over every run on disk. meta/score are projected through the field
    whitelist here, which is the only place either file is opened."""
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
            chat, cost = parse_chat(os.path.join(rdir, "transcript.jsonl"))
            polluted = (meta.get("versions") or {}).get("harness") != CURRENT_HARNESS
            ji = score.get("judge_input")   # judge telemetry: EXACTLY what the judge was shown. Absent
            ji = _pick(ji, JUDGE_INPUT_FIELDS) if isinstance(ji, dict) else None   # on every score.json
            rows[(meta["model"], meta["tool"], tid)] = {   # written before judge.py started recording it
                "meta": meta, "verdict": score.get("verdict"),
                "judge": score.get("judge_model"),   # the model that actually scored THIS run
                "reason": score.get("reason", ""), "conf": score.get("confidence"),
                "judge_input": ji,
                "png": png if os.path.exists(png) else None,
                "chat": chat, "cost": cost,
                # A result produced by an older/polluted harness: shown, but slated to be overwritten
                # as soon as the current-harness queue is drained.
                "polluted": polluted}
    return rows


def shown_models(rows):
    """Models to render: internal ones are dropped in public mode, and also whenever the dataset carries
    no result rows for them — so the public branch (which ships no internal dirs) never surfaces them,
    flag or not. Non-internal models always show (pending ones render as empty columns)."""
    def ok(m):
        if not is_internal(m):
            return True
        if PUBLIC:
            return False
        return any(k[0] == m for k in rows)
    return [m for m in MODELS if ok(m)]


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


def gather(results_dir):
    """Read the dataset and apply every eligibility rule, in report.py's order. Nothing is written."""
    tasks, tmap = load_tasks()
    # Optional app filter (env): REPORT_APPS=element restricts the whole export to those apps.
    apps_env = os.environ.get("REPORT_APPS")
    if apps_env:
        keep_apps = set(apps_env.split(","))
        tasks = [t for t in tasks if t["app"] in keep_apps]
        tmap = {t["id"]: t for t in tasks}
    rows = collect(results_dir)
    # Orphan result dirs (a task id no longer in tasks.json) have no cell and no catalog entry, so they
    # would be unreachable files. Drop them here rather than emitting dead weight.
    rows = {k: v for k, v in rows.items() if k[2] in tmap}
    models = shown_models(rows)
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
        models = [m for m in keep if m in models] or keep   # preserve requested order
    rows = {k: v for k, v in rows.items() if k[0] in set(models)}
    return Dataset(tasks=tasks, tmap=tmap, models=models, rows=rows,
                   annulled=frozenset(t["id"] for t in tasks if t.get("annulled")))


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

    `index` is the 1-based step number the drawer prints, not the array position: the array already
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
    before judge.py started writing it (the fallback chain the current drawer applies client-side)."""
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


def tool_entry(tl):
    return {"id": tl, "label": tl_label(tl), "version": TOOL_VERSIONS.get(tl)}


def build_run_index(ds):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "scoring": {"weights": {v: SCORE[v] for v in GRADED},
                    "gradedVerdicts": list(GRADED),
                    "baselineToolId": BASELINE_TOOL},
        "catalog": {
            "models": [model_entry(m) for m in ds.models],
            "tools": [tool_entry(tl) for tl in TOOLS],
            "tasks": [{"id": t["id"], "app": t.get("app") or "", "kind": t.get("kind") or "",
                       "prompt": t.get("prompt") or "", "annulled": bool(t.get("annulled"))}
                      for t in ds.tasks],
        },
        "cells": [{"modelId": m, "toolId": tl, "taskId": t["id"],
                   "lifecycle": unit_state(ds.rows, m, tl, t["id"]),
                   "verdict": (ds.rows.get((m, tl, t["id"])) or {}).get("verdict") or None,
                   "wallSeconds": ((ds.rows.get((m, tl, t["id"])) or {}).get("meta") or {}).get("wall_s"),
                   "costUsd": (ds.rows.get((m, tl, t["id"])) or {}).get("cost")}
                  for m in ds.models for tl in TOOLS for t in ds.tasks],
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
            "screenshotHref": f"{ARTIFACT_ROOT}/{key}.webp" if row["png"] else None,
        },
        "judgeInput": judge_input_of(row, ds.tmap.get(tid) or {}),
        "steps": steps_of(events),
        # No events -> no file and href null, so "No transcript recorded" never costs a 404.
        "transcript": {"href": f"{DATA_ROOT}/transcripts/{key}.json" if events else None,
                       "eventCount": len(events)},
    }


# Three real runs anchor the methodology explanation: one success, one partial, one failure. They use
# the same rows/tasks as the rest of the export, so the examples cannot drift from published evidence.
# A dataset without them (a partial run, or a model filter that excludes gpt_high) emits none.
METHOD_SPECS = [
    (("gpt_high", "agent-device", "element-28"), "Compose", "Send “status update”"),
    (("gpt_high", "argent", "element-22"), "Search", "Search for “update”"),
    (("gpt_high", "none", "bsky-25"), "Reply", "Reply “nice one”"),
]


def build_method_examples(ds):
    examples = []
    for (m, tl, tid), action, title in METHOD_SPECS:
        row, task = ds.rows.get((m, tl, tid)), ds.tmap.get(tid)
        if not row or not task or not row["png"] or row["verdict"] not in GRADED:
            continue
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
            "screenshotHref": f"{ARTIFACT_ROOT}/{run_key(m, tl, tid)}.webp",
        })
    return examples


def build_provenance(ds, now):
    # The judge, read off the verdicts on disk rather than hardcoded, so it cannot go stale when
    # judge.py is repointed. It covers every model in this export.
    judges = sorted({row["judge"] for k, row in ds.rows.items()
                     if row["judge"] and row["verdict"] and k[2] not in ds.annulled})
    judge_str = " / ".join(judges) or JUDGE_MODEL
    apps = sorted({t["app"] for t in ds.tasks})
    return {
        "generatedAt": now,
        "judgeLine": f"judge {judge_str} vision · iOS Simulator" if judge_str else "iOS Simulator",
        "toolVersions": {tl: TOOL_VERSIONS[tl] for tl in TOOLS if TOOL_VERSIONS.get(tl)},
        # only the surfaces THIS export covers — the manifest also pins apps that are frozen but not run
        "appVersions": {a: APP_VERSIONS[a] for a in apps
                        if a in APP_VERSIONS
                        and (APP_VERSIONS[a].get("version") or APP_VERSIONS[a].get("commit"))},
    }


def build_report_meta(ds, now, build_id):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "models": [model_entry(m) for m in ds.models],
        "tools": [tool_entry(tl) for tl in TOOLS],
        "provenance": build_provenance(ds, now),
        "methodExamples": build_method_examples(ds),
        "manifest": {"schemaVersion": SCHEMA_VERSION, "buildId": build_id,
                     "dataRoot": DATA_ROOT, "artifactRoot": ARTIFACT_ROOT},
        # Deliberately no `view`: Node derives it from RunIndex, avoiding a second aggregation
        # implementation in Python.
    }


def build_resources(ds, now, build_id):
    """(relpath -> JSON payload, [(source png, relpath)]). Pure: reads a Dataset, touches no disk."""
    resources = {f"{DATA_DIR}/run-index.json": build_run_index(ds),
                 f"{DATA_DIR}/report-meta.json": build_report_meta(ds, now, build_id)}
    screenshots = []
    for (m, tl, tid), row in sorted(ds.rows.items()):
        key = run_key(m, tl, tid)
        resources[f"{DATA_DIR}/runs/{key}.json"] = build_run_detail(ds, m, tl, tid)
        if row["chat"]:
            resources[f"{DATA_DIR}/transcripts/{key}.json"] = {"schemaVersion": SCHEMA_VERSION,
                                                               "events": row["chat"]}
        if row["png"]:
            screenshots.append((row["png"], f"{ARTIFACT_DIR}/{key}.webp"))
    return resources, screenshots


# ---- writing ------------------------------------------------------------------------------------
def _assert_gated(paths, models):
    """A leaked screenshot shows up as a PATH, not as file content, so check the path set too: every
    emitted `<model>__<tool>__<task>` name must belong to a model that survived the gate."""
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


def export(ds, out_dir, now, build_id):
    """The only function that writes. It takes a Dataset and nothing else, so the gate applied in
    gather() is the gate applied to every byte on disk."""
    resources, screenshots = build_resources(ds, now, build_id)
    paths = sorted(set(resources) | {rel for _src, rel in screenshots} | {INVENTORY_REL})
    _assert_gated(paths, ds.models)
    resources[INVENTORY_REL] = {"paths": paths}

    removed = prune(out_dir, set(paths))
    for rel, payload in resources.items():
        write_json(out_dir, rel, payload)
    copied = sum(1 for src, rel in screenshots if copy_screenshot(out_dir, src, rel))
    return {"paths": len(paths), "resources": len(resources), "screenshots": len(screenshots),
            "copied": copied, "removed": removed}


def main():
    ap = argparse.ArgumentParser(description="Export the report's static JSON + screenshots.")
    ap.add_argument("--results", default=os.path.join(os.path.dirname(ROOT), "data"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(ROOT), "public"))
    a = ap.parse_args()

    ds = gather(a.results)            # every eligibility rule, before a single byte is written
    now = datetime.datetime.now()
    stats = export(ds, a.out, now.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y%m%dT%H%M%S"))

    cells = len(ds.models) * len(TOOLS) * len(ds.tasks)
    transcripts = sum(1 for r in ds.rows.values() if r["chat"])
    print(f"export -> {a.out}")
    print(f"  {len(ds.models)} models x {len(TOOLS)} tools x {len(ds.tasks)} tasks = {cells} cells"
          f" ({len(ds.annulled)} annulled)")
    print(f"  {len(ds.rows)} runs, {transcripts} transcripts, {stats['screenshots']} screenshots"
          f" ({stats['copied']} copied)")
    print(f"  {stats['paths']} paths owned, {stats['removed']} stale file(s) pruned")


if __name__ == "__main__":
    main()
