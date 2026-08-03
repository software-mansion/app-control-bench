#!/usr/bin/env python3
"""One command to see everything and fix the ledger. Preflight health, matrix coverage, and the
phantom-mark self-heal (the silent-skip bug: ledger says a unit ran, but its result was purged, so the
runner skips it forever). Run this before a batch (catch a broken surface before burning API spend) and
after (confirm full coverage, no masked gaps).

    python3 runner/doctor.py                        # health + coverage + phantom report (read-only)
    python3 runner/doctor.py --heal                 # also reset phantom-masked units so they re-run
    python3 runner/doctor.py --models gpt,gpt_high --apps bluesky
    python3 runner/doctor.py --preflight            # only the surface health checks (fast)

Exit code 0 iff healthy AND (no phantoms, or they were healed). Non-zero otherwise — scriptable.
"""
import os, sys, json, socket, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench, bench_env, ledger, isolation, sim_device

# Auto-heal is safe for the intended single-host model, where the results dir IS the source of truth and
# a phantom means a genuinely-purged result (usually 1-few). A LARGE phantom count almost always means the
# ledger was seeded from another host (cross-machine harvest) — those units live elsewhere, so mass-resetting
# them would wastefully re-run everything. Above this many, heal refuses without an explicit force.
HEAL_AUTO_MAX = 10

G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; X = "\033[0m"
def ok(s):   return f"{G}OK{X}  {s}"
def bad(s):  return f"{R}FAIL{X} {s}"
def warn(s): return f"{Y}WARN{X} {s}"


def port_up(port):
    s = socket.socket(); s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def auth_keys():
    """Provider ids present in the opencode auth store (checks both known locations)."""
    for p in (os.path.expanduser("~/.local/share/opencode/auth.json"),
              os.path.expanduser("~/.opencode/auth.json")):
        try:
            return set(json.load(open(p)).keys()), p
        except Exception:
            continue
    return set(), None


def preflight(need_providers):
    fails = 0
    print("== ENVIRONMENT ==")
    r = bench_env.resolve()
    for label, path in (("node", r["node"]), ("opencode", r["opencode"]),
                        ("agent-device", r["agent_device"]), ("argent", r["argent"])):
        # argent/node may be bare names resolved on PATH; treat a bare name as present
        present = os.path.exists(path) or (os.sep not in path)
        print(" ", (ok if present else bad)(f"{label}: {path}"))
        fails += 0 if present else 1

    print("== ISOLATION (lock / golden / device pool) ==")
    holder = isolation.lock_holder()
    if holder:
        print(" ", bad(f"device lock HELD by: {holder} (one stream at a time — wait or --wait-lock)"))
        fails += 1
    else:
        print(" ", ok("device lock free"))
    try:
        g = sim_device.check_golden()
        print(" ", ok(f"golden {g['name']} present + Shutdown"))
    except sim_device.DeviceError as e:
        print(" ", bad(f"golden: {e}")); fails += 1
    try:
        devs = sim_device._devices()
        stale = [d["name"] for d in devs if d["name"].startswith(sim_device.CLONE_PREFIX)]
        booted = [d["name"] for d in devs if d["state"] == "Booted"]
        print(" ", (warn if stale else ok)(f"stale bench-run-* clones: {stale or 'none'}"
                                           + (" (reaped automatically at next run)" if stale else "")))
        if booted:
            print(" ", bad(f"booted sim(s) present: {booted} — everything must be shut down between runs"))
            fails += 1
        else:
            print(" ", ok("no booted simulators (correct resting state)"))
    except sim_device.DeviceError as e:
        print(" ", warn(f"device list unavailable: {e}"))

    print("== PROXIES (per-run now; down is the CORRECT resting state) ==")
    for port, what in ((8788, "anthropic-thinking"), (8790, "openai-reasoning")):
        up = port_up(port)
        # a listener between runs is a leak (or a foreign process squatting the port)
        print(" ", (warn if up else ok)(f":{port} {what} {'UP between runs — leaked/foreign?' if up else 'down'}"))

    print("== AUTH (opencode API keys) ==")
    keys, src = auth_keys()
    if not src:
        print(" ", bad("no opencode auth.json found")); fails += 1
    else:
        for prov in sorted(need_providers):
            present = prov in keys
            print(" ", (ok if present else bad)(f"{prov} key {'present' if present else 'MISSING'} ({src})"))
            fails += 0 if present else 1
    return fails


def preflight_hooks(apps):
    """Server-reset hook prerequisites for the apps in scope — a missing creds file / baseline ts /
    seed config passes a naive preflight but hard-aborts the matrix on the very first unit
    (ResetError). Catch it here instead, before any API spend."""
    fails = 0
    print("== RESET HOOKS (per-app server-reset prerequisites) ==")
    if "bluesky" in apps:
        # Self-hosted atproto path: the reset hook is `curl :1987/reset` against the persistent
        # dev-env control server (tasks/setup/bluesky/bench-server.ts). Probe /info: it must be up
        # and report the bench account, or every bluesky run's reset_pre hard-fails.
        import urllib.request
        try:
            with urllib.request.urlopen("http://localhost:1987/info", timeout=5) as r:
                info = json.loads(r.read().decode() or "{}")
            did = (info.get("bench") or {}).get("did", "")
            n_acc = len(info.get("accounts") or {})
            if did and n_acc:
                print(" ", ok(f"bluesky dev-env control API :1987 up (bench {did[:16]}…, {n_acc} accounts)"))
            else:
                print(" ", bad("bluesky :1987/info reachable but missing bench/accounts — reseed bench-server.ts"))
                fails += 1
        except Exception as e:
            print(" ", bad(f"bluesky dev-env control API :1987 down ({type(e).__name__}) — "
                           "start tasks/setup/bluesky/bench-server.ts (reset hooks will hard-fail)"))
            fails += 1
    if "element" in apps and not port_up(8008):
        print(" ", bad("synapse :8008 not answering (element seed.py will hard-fail)")); fails += 1
    if not fails:
        print(" ", ok("hook prerequisites satisfied for: " + ", ".join(sorted(apps))))
    return fails


def coverage(models, tools, tasks, do_heal, heal_force=False):
    print("== COVERAGE ==")
    total_pending = 0
    active = ledger.reconcile(bench.RESULTS, models, tools, tasks, persist=False)
    tids = [t["id"] if isinstance(t, dict) else t for t in tasks]
    for tl in tools:
        for m in models:
            cell = f"{m}__{tl}"
            done = sum(1 for t in tids if ledger.has_run(bench.RESULTS, m, tl, t))
            pend = len(tids) - done
            if done == 0 and pend == len(tids) and not os.path.isdir(os.path.join(bench.RESULTS, cell)):
                continue  # never-touched cell: skip to keep the table readable
            total_pending += pend
            flag = "" if pend == 0 else f"  <-- {pend} pending"
            print(f"  {cell:28s} {done:3d}/{len(tids)}{flag}")
    print(f"  ---- total pending across shown cells: {total_pending}")

    ph = ledger.phantoms(bench.RESULTS, models, tools, tasks)
    print("== PHANTOM-MASKED GAPS (ledger=done, no result on disk) ==")
    if not ph:
        print(" ", ok("none — every 'done' unit has a real result on disk"))
    else:
        for uid in ph[:40]:
            print(" ", warn(uid))
        if len(ph) > 40:
            print(" ", warn(f"... and {len(ph) - 40} more"))
        if do_heal and (len(ph) <= HEAL_AUTO_MAX or heal_force):
            healed = ledger.heal(bench.RESULTS, models, tools, tasks)
            print(" ", ok(f"HEALED {len(healed)} unit(s) -> pending; they will re-run"))
            ph = []
        elif do_heal:
            print(" ", bad(f"REFUSING to heal {len(ph)} units (> {HEAL_AUTO_MAX}) — this many phantoms usually "
                           f"means the ledger was seeded from another host, whose results live elsewhere. "
                           f"Re-run with --heal-force ONLY if these are genuinely purged local runs."))
        else:
            print(" ", bad(f"{len(ph)} masked gap(s) — rerun with --heal to reset them"))
    return total_pending, len(ph)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="comma list (default: all with any activity)")
    ap.add_argument("--tools", default=",".join(bench.TOOLS))
    ap.add_argument("--apps", help="comma list of apps (default: all enabled in bench.APPS)")
    ap.add_argument("--heal", action="store_true", help="reset phantom-masked units to pending (refuses a mass-heal)")
    ap.add_argument("--heal-force", action="store_true", help="heal even a large phantom set (cross-host ledgers)")
    ap.add_argument("--preflight", action="store_true", help="only run surface health checks")
    a = ap.parse_args()

    tools = a.tools.split(",")
    apps = a.apps.split(",") if a.apps else [k for k in bench.APPS]
    tasks = [t for t in bench.load_tasks() if t["app"] in apps and t["app"] in bench.APPS and not t.get("annulled")]
    models = a.models.split(",") if a.models else list(bench.MODELS.keys())

    # providers needed = union over the scoped models (anthropic/openai/ollama)
    need = set()
    for m in models:
        mid = bench.MODELS.get(m, "")
        if mid.startswith("anthropic/"): need.add("anthropic")
        elif mid.startswith("openai/"):  need.add("openai")
    fails = preflight(need)
    fails += preflight_hooks(set(apps) & set(bench.APPS))
    if a.preflight:
        sys.exit(1 if fails else 0)

    print()
    pending, phantoms_left = coverage(models, tools, tasks, a.heal or a.heal_force, a.heal_force)

    print("\n== SUMMARY ==")
    print(f"  preflight failures: {fails}")
    print(f"  runs pending:       {pending}")
    print(f"  masked gaps left:   {phantoms_left}")
    sys.exit(1 if (fails or phantoms_left) else 0)


if __name__ == "__main__":
    main()
