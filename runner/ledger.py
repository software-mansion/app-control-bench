"""Idempotent, append-only result ledger — asynchronous result submission for the benchmark.

A *unit* = (model, variant, task), keyed by a stable id `model__variant__task`. A **variant** is one
version of one toolkit (`argent@0.15.0`), because that is what a result is a result *of*: two versions of
a toolkit are two different sets of affordances and must not land on top of each other. Each unit moves
through a monotonic DAG of states that can only PROGRESS, never regress:

    pending  ->  completed  ->  judged

  - pending:   no valid run yet.
  - completed: a real run exists on the correct surface — meta.json that is NOT a SURFACE_ERROR, plus a
               final.png. The agent did the task and the screenshot was captured. Awaiting the (async) judge.
  - judged:    a vision verdict (success|partial|fail) has been recorded. Terminal.

Guarantees:
  - A unit is NEVER re-run once it reaches `completed`, and its verdict is NEVER recomputed once `judged`.
    Submission is asynchronous: the runner advances units to `completed`, the judge advances them to
    `judged`, independently and incrementally — no global wipe, ever.
  - Adding a new model introduces fresh `pending` units for every (variant, task) permutation and touches
    no existing unit. Retired models stay on disk + in the ledger but are hidden from the active matrix
    (callers pass only the current model list to reconcile()/active views).
  - State is monotonic: derived = max(persisted_ledger_state, on_disk_state). Disk is the source of truth
    for forward progress; the ledger is the durable record so a transient missing file can't regress a unit.
"""
import os, json

ORDER = {"pending": 0, "completed": 1, "judged": 2}
VALID_VERDICTS = {"success", "partial", "fail"}
LEDGER_NAME = "ledger.json"


def variant_id(toolkit, version):
    """The benchmark's unit of comparison: one version of one toolkit. A toolkit with no version to
    record keeps its bare id — the `none` control, and runs written before the stamp existed."""
    return f"{toolkit}@{version}" if version else toolkit


def split_variant(variant):
    toolkit, _, version = variant.partition("@")
    return toolkit, (version or None)


def unit_id(model, variant, task):
    return f"{model}__{variant}__{task}"


def cell_name(model, variant):
    return f"{model}__{variant}"


def _read_json(p):
    try:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return json.load(open(p))
    except Exception:
        pass
    return None


def run_tool_version(meta):
    """The version a run was produced with: the stamp `bench.run_versions` writes into every meta.json.
    None for the no-tool baseline, which has no tool to version, and for runs written before that stamp
    existed — both live on the bare toolkit id rather than borrowing a version they cannot support."""
    return (meta.get("versions") or {}).get("tool") or None


def run_variant(meta):
    """The variant a run on disk belongs to, read from its own stamp rather than from where it sits."""
    return variant_id(meta["tool"], run_tool_version(meta))


def result_dir(results_dir, model, variant, task):
    """Where this unit's run lives. The versioned directory is the layout; a tree written before it
    names the directory after the bare toolkit, so fall back to that when it holds this exact version.
    Without the fallback every run in an un-migrated tree — the public repo, a live run dir — reads as
    unrun, and the next matrix regenerates all of it."""
    rdir = os.path.join(results_dir, cell_name(model, variant), task)
    if os.path.exists(rdir):
        return rdir
    toolkit, version = split_variant(variant)
    if version:
        legacy = os.path.join(results_dir, cell_name(model, toolkit), task)
        if run_tool_version(_read_json(os.path.join(legacy, "meta.json")) or {}) == version:
            return legacy
    return rdir


def disk_state(results_dir, model, variant, task):
    """Derive a unit's state purely from what's on disk."""
    rdir = result_dir(results_dir, model, variant, task)
    meta = _read_json(os.path.join(rdir, "meta.json"))
    if not meta or not os.path.exists(os.path.join(rdir, "final.png")):
        return "pending"
    if meta.get("returncode") == -2:          # SURFACE_ERROR — not a real run, stays runnable
        return "pending"
    score = _read_json(os.path.join(rdir, "score.json"))
    if score and score.get("verdict") in VALID_VERDICTS:
        return "judged"
    return "completed"


def _progress(a, b):
    """Monotonic max of two states (never regress)."""
    return a if ORDER[a] >= ORDER[b] else b


def load(results_dir):
    return _read_json(os.path.join(results_dir, LEDGER_NAME)) or {"units": {}}


def save(results_dir, ledger):
    os.makedirs(results_dir, exist_ok=True)
    tmp = os.path.join(results_dir, LEDGER_NAME + ".tmp")
    json.dump(ledger, open(tmp, "w"), indent=2)
    os.replace(tmp, os.path.join(results_dir, LEDGER_NAME))   # atomic; safe alongside a running bench


def _entry(results_dir, model, variant, task, prev):
    state = _progress(disk_state(results_dir, model, variant, task), prev.get("state", "pending"))
    e = {"model": model, "variant": variant, "task": task, "state": state}
    if state == "judged":
        sc = _read_json(os.path.join(result_dir(results_dir, model, variant, task), "score.json")) or {}
        e["verdict"] = sc.get("verdict") or prev.get("verdict")
    return e


def mark(results_dir, model, variant, task):
    """Advance one unit from disk (monotonic) and persist. Call after a run or a judge writes its file."""
    ledger = load(results_dir)
    units = ledger.setdefault("units", {})
    uid = unit_id(model, variant, task)
    units[uid] = _entry(results_dir, model, variant, task, units.get(uid, {}))
    save(results_dir, ledger)
    return units[uid]["state"]


def reconcile(results_dir, models, variants, tasks, persist=True):
    """Sync the ledger with disk for the ACTIVE matrix (models passed in). Monotonic; never regresses.
    Returns {uid: {model, variant, task, state, verdict?}} for the active units only (retired models
    hidden). `variants` is what this machine would produce now, so a version bump reads as pending."""
    ledger = load(results_dir)
    allunits = ledger.setdefault("units", {})
    active = {}
    for m in models:
        for v in variants:
            for t in tasks:
                tid = t["id"] if isinstance(t, dict) else t
                uid = unit_id(m, v, tid)
                allunits[uid] = _entry(results_dir, m, v, tid, allunits.get(uid, {}))
                active[uid] = allunits[uid]
    if persist:
        save(results_dir, ledger)
    return active


def state_of(results_dir, model, variant, task):
    prev = load(results_dir).get("units", {}).get(unit_id(model, variant, task), {})
    return _progress(disk_state(results_dir, model, variant, task), prev.get("state", "pending"))


def has_run(results_dir, model, variant, task):
    """True once a unit is completed or judged — a valid result exists on disk (any harness)."""
    return ORDER[state_of(results_dir, model, variant, task)] >= ORDER["completed"]


def run_harness(results_dir, model, variant, task):
    """The `versions.harness` stamped on this unit's result, or None if unrun / no stamp (legacy)."""
    meta = _read_json(os.path.join(result_dir(results_dir, model, variant, task), "meta.json")) or {}
    return (meta.get("versions") or {}).get("harness")


def is_polluted(results_dir, model, variant, task, harness):
    """A completed/judged unit whose result was produced by a DIFFERENT (older/polluted) harness — kept
    for display but slated to be overwritten. False for unrun units and for current-harness results."""
    return has_run(results_dir, model, variant, task) and run_harness(results_dir, model, variant, task) != harness


def needs_run(results_dir, model, variant, task, harness):
    """The runner should (re)run this unit: either it has NO valid result, or its only result is from a
    stale/polluted harness (regenerate over it). This is what makes the current pass overwrite pre-skill
    and always-inject pollution rather than skip it forever."""
    return (not has_run(results_dir, model, variant, task)) or \
           run_harness(results_dir, model, variant, task) != harness


def summary(active_units):
    """Count active units by state."""
    c = {"pending": 0, "completed": 0, "judged": 0}
    for u in active_units.values():
        c[u["state"]] = c.get(u["state"], 0) + 1
    return c


def phantoms(results_dir, models, variants, tasks):
    """Units the *persisted* ledger claims are completed/judged but which have NO valid result on disk
    (dir purged, files removed, or only a SURFACE_ERROR meta). has_run() returns True for these, so the
    runner SKIPS them forever — a silent gap. Returns the list of offending uids. Read-only."""
    units = load(results_dir).get("units", {})
    out = []
    for m in models:
        for v in variants:
            for t in tasks:
                tid = t["id"] if isinstance(t, dict) else t
                e = units.get(unit_id(m, v, tid))
                if not e:
                    continue
                persisted = e.get("state", "pending")
                if ORDER[persisted] >= ORDER["completed"] and disk_state(results_dir, m, v, tid) == "pending":
                    out.append(unit_id(m, v, tid))
    return out


def heal(results_dir, models, variants, tasks):
    """Reset phantom-masked units (see phantoms()) so the runner will re-run them. Deletes the stale
    persisted entry; its derived state falls back to disk (=pending). Idempotent. Returns healed uids."""
    healed = phantoms(results_dir, models, variants, tasks)
    if healed:
        led = load(results_dir)
        for uid in healed:
            led.get("units", {}).pop(uid, None)
        save(results_dir, led)
    return healed
