#!/usr/bin/env python3
"""Run the main matrix: cloud models + reasoning-effort variants x both tools x all bluesky tasks.

Fully isolated (see bench.run_one): every unit gets a fresh golden-clone device, fresh per-run
proxies (effort fixed via BENCH_EFFORT_JSON from bench.EFFORT — no shared control file), and a
verified total teardown. The host-wide device lock makes a second concurrent stream fail loudly.
Serial and resumable (skips completed units via the ledger).

    nohup python3 runner/run_matrix.py > /tmp/bench-matrix.log 2>&1 &

Two ways to say WHAT runs:
  * The QUEUE (runner/queue.json, edited via runner/dashboard.py) — enabled entries in priority
    order, each an (model, tool, apps) triple. This is the default when queue.json exists.
  * Env overrides (legacy / ad-hoc): ONLY=key1,key2 (models), TOOLS=argent, TASK=bsky-01,
    APPS=bluesky,element. When any of these is set they take precedence over the queue.

    nohup python3 runner/run_matrix.py > /tmp/bench-matrix.log 2>&1 &
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench, ledger, isolation, sim_device

# Fallback ordering when neither a queue nor ONLY= is given; effort comes from bench.EFFORT.
PLAN = ["gpt_none", "gpt_low", "gpt", "gpt_high", "gpt55",
        "haiku", "haiku_low", "haiku_med", "haiku_high", "opus"]
_missing = [k for k in PLAN if k not in bench.EFFORT]
assert not _missing, f"PLAN keys missing from bench.EFFORT: {_missing}"
QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.json")


def plan_from_queue():
    """(tool, model, task_ids) work items from the dashboard queue — enabled entries, priority
    order. Returns None if there is no queue or env overrides are set (env wins)."""
    if any(os.environ.get(v) for v in ("ONLY", "TOOLS", "TASK", "APPS")):
        return None
    q = ledger._read_json(QUEUE_PATH)
    if not q or not q.get("entries"):
        return None
    by_app = {}
    for t in bench.load_tasks():
        if t["app"] in bench.APPS and not t.get("annulled"):
            by_app.setdefault(t["app"], []).append(t["id"])
    items = []
    for e in sorted(q["entries"], key=lambda x: x.get("priority", 0)):
        if not e.get("enabled") or e.get("model") not in bench.MODELS or e.get("tool") not in bench.TOOLS:
            continue
        tids = [tid for app in (e.get("apps") or []) for tid in by_app.get(app, [])]
        if tids:
            items.append((e["tool"], e["model"], tids))
    return items or None


def regen_plan(tasks_by_id):
    """Tier 2 — the 'nothing better to do' fallback: (tool, model, task_ids) work items for POLLUTED
    cells found on disk (a completed result from an older/polluted harness) that are NOT in the queue,
    for models still in bench.MODELS and apps this machine can run. Appended AFTER the queue so faithful
    current-harness work always goes first; each unit's needs_run() check then overwrites the pollution.
    Opt-in via REGEN_POLLUTED=1 — regenerating the full historical roster is real API spend, so it must
    be switched on deliberately, not entered by default."""
    by_cell = {}
    for cell in sorted(os.listdir(bench.RESULTS) if os.path.isdir(bench.RESULTS) else []):
        cdir = os.path.join(bench.RESULTS, cell)
        if not os.path.isdir(cdir) or "__" not in cell:
            continue
        model, tool = cell.split("__", 1)
        if model not in bench.MODELS or tool not in bench.TOOLS:
            continue
        for tid in sorted(os.listdir(cdir)):
            t = tasks_by_id.get(tid)
            if not t or t["app"] not in bench.APPS or t.get("annulled"):
                continue
            if ledger.is_polluted(bench.RESULTS, model, tool, tid, bench.HARNESS):
                by_cell.setdefault((tool, model), []).append(tid)
    return [(tool, model, tids) for (tool, model), tids in by_cell.items()]


def main():
    isolation.acquire_lock(wait="--wait-lock" in sys.argv, label="run_matrix")
    tasks_by_id = {t["id"]: t for t in bench.load_tasks()}

    queue_items = plan_from_queue()
    if queue_items is not None:
        work = queue_items
        print(f"matrix: {len(work)} queue entries (runner/queue.json)", flush=True)
        if os.environ.get("REGEN_POLLUTED") == "1":
            regen = regen_plan(tasks_by_id)
            work = work + regen           # after the queue; faithful work first, then overwrite pollution
            print(f"matrix: +{len(regen)} polluted regen cells (REGEN_POLLUTED=1)", flush=True)
    else:
        only = set(os.environ["ONLY"].split(",")) if os.environ.get("ONLY") else None
        tools = os.environ.get("TOOLS", ",".join(bench.TOOLS)).split(",")
        one_task = os.environ.get("TASK")
        apps = set(os.environ.get("APPS", "bluesky").split(","))
        tids = [t["id"] for t in bench.load_tasks()
                if t["app"] in apps and t["app"] in bench.APPS and not t.get("annulled")
                and (not one_task or t["id"] == one_task)]
        plan = [k for k in PLAN if not only or k in only]
        # tool-outer so the fast tool (argent) completes across ALL models before the slow one.
        work = [(tool, key, tids) for tool in tools for key in plan]
        print(f"matrix: {len(plan)} models x {len(tools)} tools x {len(tids)} tasks", flush=True)

    sim_device.check_golden()   # fail fast before burning any API spend

    for tool, key, tids in work:
        # needs_run (not has_run): a unit whose only result is from a stale/polluted harness (pre-skill
        # v1, or the always-inject v2) is RErun so the current pass overwrites the pollution in place,
        # instead of skipping it forever.
        pending = [tasks_by_id[tid] for tid in tids
                   if tid in tasks_by_id and ledger.needs_run(bench.RESULTS, key, tool, tid, bench.HARNESS)]
        if not pending:
            continue
        print(f"=== {tool} / {key}  effort={bench.EFFORT.get(key) or 'none'}  "
              f"({len(pending)} pending) ===", flush=True)
        for t in pending:
            try:
                bench.run_one(key, tool, t)   # (bluesky session refresh happens inside, per-run)
            except (isolation.TeardownError, isolation.ResetError, sim_device.DeviceError):
                # isolation can no longer be guaranteed (unkillable procs / failed server reset /
                # bad golden / foreign booted sim) — continuing would produce polluted results.
                raise
            except Exception as e:
                print(f"  {key}:{tool}/{t['id']} ERROR {e}", flush=True)
    print("=== matrix done ===", flush=True)


if __name__ == "__main__":
    main()
