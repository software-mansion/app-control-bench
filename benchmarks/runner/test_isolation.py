#!/usr/bin/env python3
"""Local unit tests for isolation.py — no devices, no network, no real bench processes.
Deliberately SAFE on a shared machine: teardown is tested against dummy `sleep` processes with
bespoke cmdline markers (KILL_PATTERNS is monkeypatched), never against the real patterns.

    python3 runner/test_isolation.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolation

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


def test_lock():
    lock = os.path.join(tempfile.mkdtemp(prefix="acb-test-"), "lock")
    # holder: a child that acquires and sleeps
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r});"
         f"import os; os.environ['BENCH_LOCK']={lock!r};"
         "import isolation; isolation.LOCK_PATH=os.environ['BENCH_LOCK'];"
         "isolation.acquire_lock(label='holder'); print('HELD', flush=True);"
         "import time; time.sleep(30)"],
        stdout=subprocess.PIPE, text=True)
    line = holder.stdout.readline()
    check("lock: holder acquired", line.strip() == "HELD", line)
    # contender: must exit nonzero with the loud message
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r});"
         "import isolation; isolation.LOCK_PATH=" + repr(lock) + ";"
         "isolation.acquire_lock(label='contender')"],
        capture_output=True, text=True)
    check("lock: second stream refused", r.returncode != 0)
    check("lock: refusal names the holder", "holder" in r.stderr and "ONE device at a time" in r.stderr,
          r.stderr[-200:])
    # lock_holder() sees it
    old = isolation.LOCK_PATH
    isolation.LOCK_PATH = lock
    h = isolation.lock_holder()
    check("lock: lock_holder reports holder", h is not None and "holder" in h, str(h))
    holder.kill()
    holder.wait()
    time.sleep(0.2)
    check("lock: kernel releases on death", isolation.lock_holder() is None)
    isolation.LOCK_PATH = old


def test_teardown_dummies():
    marker = f"acb-dummy-{os.getpid()}"
    # isolate the registry so the test never touches a real bench's pid file
    old_pids_path = isolation.PIDS_PATH
    isolation.PIDS_PATH = os.path.join(tempfile.mkdtemp(prefix="acb-test-"), "pids.json")
    # marker lives INSIDE the -c string (a bare `sleep` would get exec-optimized and lose the
    # cmdline). Each owned dummy also forks a CHILD sleep whose cmdline does NOT match — the
    # tree-kill must take it down with its parent. Owned = registered at spawn, like the harness.
    procs = [subprocess.Popen(["bash", "-c", f"true {marker}-{i}; sleep 300 & wait"],
                              start_new_session=True) for i in range(2)]
    for p in procs:
        isolation.register_pid(p.pid, "test-dummy")
    # NOT registered; DEVNULL stdout so its orphaned child can't pin our pipe after foreign.kill(),
    # and a short sleep so that orphan doesn't linger long after the test
    foreign = subprocess.Popen(["bash", "-c", f"true {marker}-foreign; sleep 45"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    check("teardown: dummies visible to pgrep", len(isolation._pgrep(marker)) == 3)
    kids = [k for p in procs for k in isolation._descendants([p.pid])]
    check("teardown: owned dummies have children", len(kids) == 2, str(kids))
    old_pat, old_scope = isolation.KILL_PATTERNS, isolation.KILL_SCOPE
    isolation.KILL_PATTERNS, isolation.KILL_SCOPE = [marker], "owned"
    try:
        rep = isolation.teardown_all("pre")
    finally:
        isolation.KILL_PATTERNS, isolation.KILL_SCOPE, isolation.PIDS_PATH = \
            old_pat, old_scope, old_pids_path
    killed = set(rep["killed"][0]["pids"])
    check("teardown: report lists owned pids + their children",
          killed == {p.pid for p in procs} | set(kids), str(rep))
    check("teardown: children are dead too", isolation._alive(kids) == [])
    check("teardown: stray (unregistered) match survives + is reported",
          isolation._alive([foreign.pid]) == [foreign.pid]
          and rep["killed"][0]["strays"] == [foreign.pid], str(rep))
    foreign.kill()
    foreign.wait()
    for p in procs:
        p.wait()
    check("teardown: nothing owned remains", isolation._pgrep(marker) == [])


def test_run_opencode_timeout():
    # a stubborn tree: parent bash spawns a child sleep; killpg must take both
    t0 = time.time()
    with tempfile.TemporaryFile(mode="w+") as tf:
        rc, err, timed_out = isolation.run_opencode(
            ["bash", "-c", "sleep 120 & sleep 120"], cwd="/tmp", timeout=2, stdout_file=tf)
    check("run_opencode: timeout flagged", timed_out and rc == -1 and err == "TIMEOUT", f"{rc} {err}")
    check("run_opencode: returned promptly", time.time() - t0 < 20, f"{time.time() - t0:.1f}s")


def test_reset_hook():
    outdir = tempfile.mkdtemp(prefix="acb-test-hook-")
    rep = isolation.run_reset_hook(["bash", "-c", "echo seeded"], outdir, timeout=10, label="ok-hook")
    check("reset_hook: success rc=0", rep["rc"] == 0 and rep["duration_s"] >= 0, str(rep))
    check("reset_hook: log captured", "seeded" in open(os.path.join(outdir, "reset-server.log")).read())
    try:
        isolation.run_reset_hook(["bash", "-c", "exit 3"], outdir, timeout=10, label="bad-hook")
        check("reset_hook: hard-fail raises", False)
    except isolation.ResetError:
        check("reset_hook: hard-fail raises", True)
    os.environ["BENCH_RESET_SOFT"] = "1"
    try:
        rep = isolation.run_reset_hook(["bash", "-c", "exit 3"], outdir, timeout=10, label="soft-hook")
        check("reset_hook: soft mode continues", rep["rc"] == 3, str(rep))
    finally:
        del os.environ["BENCH_RESET_SOFT"]


def test_env_manifest():
    outdir = tempfile.mkdtemp(prefix="acb-test-env-")
    p = isolation.write_env_manifest(outdir, ts_start=1, effort={"gpt54": "high"}, clone_udid="X")
    d = json.load(open(p))
    check("env.json: round-trips", d["schema"] == 1 and d["effort"] == {"gpt54": "high"} and d["clone_udid"] == "X")


def test_providers_needed():
    check("providers: anthropic", isolation.providers_needed("anthropic/claude-haiku-4-5") == ["anthropic"])
    check("providers: openai", isolation.providers_needed("openai/gpt-5.4-mini") == ["openai"])
    check("providers: ollama none", isolation.providers_needed("ollama/silver-v8:e4b") == [])


if __name__ == "__main__":
    test_lock()
    test_teardown_dummies()
    test_run_opencode_timeout()
    test_reset_hook()
    test_env_manifest()
    test_providers_needed()
    print(f"\nall {PASS} checks passed")
