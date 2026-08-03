#!/usr/bin/env python3
"""Run isolation: single-device lock, total process teardown, per-run proxies, process-group
opencode execution, server-reset hooks, and the per-run env.json audit manifest.

The mandate this module enforces: EVERYTHING is terminated between runs (verified, not hoped),
and exactly ONE bench stream / ONE device exists at any moment. Teardown runs at BOTH ends of a
run (pre = heal a crashed previous invocation, post = guarantee run N never leaks into N+1).

Stdlib only. All processes are matched by their exact bench-specific cmdlines so an unrelated
opencode/argent on the machine is never touched.
"""
import fcntl
import json
import os
import signal
import socket
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- single-device lock

LOCK_PATH = os.environ.get("BENCH_LOCK", "/tmp/app-control-bench.device.lock")
_lock_fd = None   # kept for process lifetime; the kernel releases the flock when we die


def acquire_lock(wait=False, label=""):
    """Take the host-wide exclusive bench lock (ONE device / ONE stream, no exceptions).
    flock is released by the kernel on process death, so no staleness detection is needed —
    and the lock lives in /tmp (per-host), never in the rsynced results/ tree."""
    global _lock_fd
    if _lock_fd is not None:
        return _lock_fd
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        holder = ""
        try:
            holder = os.read(fd, 256).decode(errors="replace").strip()
        except OSError:
            pass
        os.close(fd)
        raise SystemExit(f"another bench stream holds the device lock ({holder or 'unknown'}); "
                         f"ONE device at a time — use --wait-lock to queue")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{os.getpid()} {socket.gethostname()} {time.strftime('%Y-%m-%dT%H:%M:%S')} {label}\n".encode())
    _lock_fd = fd
    return fd


def lock_holder():
    """The 'pid host ts label' line of the current holder, or None if the lock is free."""
    try:
        fd = os.open(LOCK_PATH, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None   # nobody holds it exclusively
        except BlockingIOError:
            return os.read(fd, 256).decode(errors="replace").strip() or "unknown"
    finally:
        os.close(fd)


# ---------------------------------------------------------------- total teardown

class TeardownError(RuntimeError):
    """Raised when a kill target survives SIGKILL — the machine can't be trusted to be clean."""


class ProxyError(RuntimeError):
    """A per-run proxy failed to start (ordinary transient failure — NOT an isolation violation;
    the unit stays pending and the matrix continues)."""


# Cmdline fragments of every process the bench (or its tools) can leave behind. The agent-device
# XCTest daemon is killed as a PROCESS only (never `clean:daemon`): its build artifacts persist on
# the host, so relaunch is cheap.
KILL_PATTERNS = [
    "opencode run --format json",
    "argent mcp",
    "tool-server.cjs",
    "agent-device.mjs mcp",
    "agent-device/dist/src/internal/daemon.js",
    "anthropic-thinking-proxy.js",
    "openai-reasoning-proxy.js",
]

# Kill scope. The patterns above are product-generic, and on a SHARED machine other agents run
# their own argent/opencode/agent-device processes — pattern-killing those would wreck their
# sessions (macOS offers no reliable way to read another process's env, so ownership can't be
# marker-based). Instead the harness RECORDS every pid it spawns (plus its process group) in a
# per-host registry file; "owned" = registered pid (start-time-verified against pid reuse) OR a
# member of a registered process group OR a live descendant of one. In the default "owned" scope
# only those are killed; pattern matches beyond them are loudly REPORTED as strays, never killed.
# On the dedicated bench host set BENCH_KILL_SCOPE=all (run_all.sh does) to reap ANY match —
# needed for full orphan-reaping guarantees (opencode's detached double-fork setsids out of every
# ownership signal), and safe there because nothing else runs.
KILL_SCOPE = os.environ.get("BENCH_KILL_SCOPE", "owned")   # "owned" | "all"
PIDS_PATH = os.environ.get("BENCH_PIDS", "/tmp/app-control-bench.pids.json")


def _pgrep(pattern):
    r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()] if r.returncode == 0 else []


def _ps_field(pid, field):
    r = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def register_pid(pid, label):
    """Record a bench-spawned pid (with launch time + pgid) so teardown can prove ownership even
    across a harness crash. The file is per-host and self-pruning."""
    reg = _registry()
    reg[str(pid)] = {"lstart": _ps_field(pid, "lstart"), "pgid": _ps_field(pid, "pgid"),
                     "label": label, "ts": int(time.time())}
    with open(PIDS_PATH, "w") as f:
        json.dump(reg, f, indent=2)


def _registry():
    try:
        with open(PIDS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _live_pgids():
    r = subprocess.run(["ps", "-axo", "pgid="], capture_output=True, text=True)
    return set(r.stdout.split())


def _prune_registry():
    """Drop registry entries with nothing left to own. An entry stays while its pid is alive
    (start-time verified against pid reuse) OR its process GROUP still has live members — a dead
    group leader (crashed/killed opencode) must keep vouching for its surviving children, which is
    the whole crash-heal point of the registry."""
    live_groups = _live_pgids()
    reg = {p: e for p, e in _registry().items()
           if _ps_field(int(p), "lstart") == e.get("lstart")
           or (e.get("pgid") and e["pgid"] in live_groups)}
    with open(PIDS_PATH, "w") as f:
        json.dump(reg, f, indent=2)
    return reg


def _descendants(pids):
    """Transitive children of `pids` (one ps snapshot) — a killed parent's helpers (e.g. a spawned
    simulator-server) often have non-matching cmdlines and must die with it."""
    r = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True)
    kids = {}
    for line in r.stdout.splitlines():
        try:
            pid, ppid = map(int, line.split())
            kids.setdefault(ppid, []).append(pid)
        except ValueError:
            continue
    out, frontier = set(), list(pids)
    while frontier:
        for c in kids.get(frontier.pop(), []):
            if c not in out:
                out.add(c)
                frontier.append(c)
    return out


def _owned_pids():
    """Every live pid we can PROVE is bench-spawned: registry pids (start-time verified), members
    of registered process groups, and their descendants."""
    reg = _prune_registry()
    owned = {int(p) for p in reg}
    pgids = {e["pgid"] for e in reg.values() if e.get("pgid")}
    if pgids:
        r = subprocess.run(["ps", "-axo", "pid=,pgid="], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] in pgids:
                owned.add(int(parts[0]))
    return owned | _descendants(owned)


def _kill_pids(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _alive(pids):
    """Pids that are still genuinely running. Zombies don't count: os.kill(pid, 0) succeeds on a
    dead-but-unreaped child (its parent hasn't wait()ed yet), which would read as an unkillable
    survivor — check the process state instead."""
    if not pids:
        return []
    r = subprocess.run(["ps", "-o", "pid=,state=", "-p", ",".join(map(str, pids))],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[1].startswith("Z"):
            out.append(int(parts[0]))
    return out


def _kill_pattern(pattern, owned=None, term_wait=5.0, kill_wait=5.0):
    """Kill every matching process in scope, plus all its descendants: TERM, poll, escalate to
    KILL, verify. Kills by exact pid (never `pkill -f` the raw pattern), so someone's
    `vim tool-server.cjs` is never touched. In 'owned' scope, matches we can't prove are ours are
    returned as `strays` (reported, not killed)."""
    matched = _pgrep(pattern)
    strays = []
    if KILL_SCOPE != "all":
        strays = [p for p in matched if p not in owned]
        matched = [p for p in matched if p in owned]
    if not matched:
        return {"pattern": pattern, "pids": [], "escalated": False, "remaining": [], "strays": strays}
    targets = sorted(set(matched) | _descendants(matched))
    _kill_pids(targets, signal.SIGTERM)
    deadline = time.time() + term_wait
    while time.time() < deadline and _alive(targets):
        time.sleep(0.25)
    escalated = bool(_alive(targets))
    if escalated:
        _kill_pids(_alive(targets), signal.SIGKILL)
        deadline = time.time() + kill_wait
        while time.time() < deadline and _alive(targets):
            time.sleep(0.25)
    return {"pattern": pattern, "pids": targets, "escalated": escalated,
            "remaining": _alive(targets), "strays": strays}


def teardown_all(phase, adev=None, adev_udid=None):
    """Kill every bench-owned process and VERIFY it died. phase is 'pre' (heal leftovers from a
    crashed/earlier invocation) or 'post' (reap the run that just finished — runs in finally).
    A survivor after SIGKILL raises TeardownError: continuing would violate the isolation mandate.
    In 'owned' scope (shared machine), pattern matches that can't be proven bench-spawned are
    printed as strays and left alone — full reaping guarantees need BENCH_KILL_SCOPE=all on a
    dedicated host."""
    # courtesy first: let agent-device close its session cleanly before we shoot the daemon
    if adev and adev_udid:
        try:
            subprocess.run([adev, "close", "--platform", "ios", "--udid", adev_udid],
                           capture_output=True, timeout=15)
        except (subprocess.TimeoutExpired, OSError):
            pass
    owned = _owned_pids() if KILL_SCOPE != "all" else None
    report = {"phase": phase, "ts": int(time.time()), "scope": KILL_SCOPE, "killed": []}
    for pattern in KILL_PATTERNS:
        report["killed"].append(_kill_pattern(pattern, owned=owned))
    strays = [(k["pattern"], k["strays"]) for k in report["killed"] if k.get("strays")]
    if strays:
        print(f"  !! teardown({phase}): unowned processes match kill patterns and were LEFT ALONE "
              f"(shared machine?): " + "; ".join(f"{p}={s}" for p, s in strays)
              + "  — set BENCH_KILL_SCOPE=all on a dedicated bench host", flush=True)
    survivors = [k for k in report["killed"] if k["remaining"]]
    if survivors:
        raise TeardownError(f"teardown({phase}): unkillable pids remain: "
                            + "; ".join(f"{k['pattern']}={k['remaining']}" for k in survivors))
    return report


# ---------------------------------------------------------------- per-run proxies

PROXY_SPECS = {   # family-prefix of MODELS[...] -> (port, script)
    "anthropic": (8788, "anthropic-thinking-proxy.js"),
    "openai":    (8790, "openai-reasoning-proxy.js"),
}


def _port_up(port):
    s = socket.socket()
    s.settimeout(0.3)
    up = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    return up


def providers_needed(model_id):
    """Which proxies a run needs, from the opencode model id ('anthropic/...', 'openai/...')."""
    return [p for p in PROXY_SPECS if model_id.startswith(p + "/")]


def start_proxies(effort, logdir, node="node", providers=()):
    """Start FRESH effort-injection proxies for this run. Effort is fixed for the proxy's lifetime
    via BENCH_EFFORT_JSON (no shared /tmp control file, no cross-stream race); logs land in the
    run's results dir. teardown_all() killed any previous instances, so the ports must be free."""
    info = {}
    env = {**os.environ, "BENCH_EFFORT_JSON": json.dumps(effort or {})}
    for prov in providers:
        port, script = PROXY_SPECS[prov]
        if _port_up(port):   # teardown just ran — a listener here is NOT ours
            raise TeardownError(f"port {port} still occupied after teardown — foreign {script}?")
        log = open(os.path.join(logdir, f"proxy-{prov}.log"), "w")
        p = subprocess.Popen([node, os.path.join(HERE, script), str(port)],
                             stdout=log, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True)
        register_pid(p.pid, f"proxy-{prov}")   # ownership proof for teardown on shared machines
        deadline = time.time() + 5
        while time.time() < deadline and not _port_up(port):
            time.sleep(0.1)
        if not _port_up(port):
            # ordinary startup failure (transient node hiccup / slow machine) — NOT an isolation
            # violation; ProxyError leaves the unit pending and the matrix continues.
            raise ProxyError(f"proxy {script} failed to listen on :{port} (see proxy-{prov}.log)")
        info[prov] = {"port": port, "pid": p.pid, "log": f"proxy-{prov}.log"}
    return info


# ---------------------------------------------------------------- opencode execution

def run_opencode(cmd, cwd, timeout, stdout_file, env=None):
    """Run opencode in its OWN process group so a timeout kills the whole tree (TERM then KILL),
    not just the tracked child, and register the pid+pgid so teardown can prove ownership of the
    whole group (MCP servers, helpers) on a shared machine. Caveat: opencode's detached double-fork
    may setsid children out of both the group and the ownership signal — post-run teardown_all()
    reaps those on a dedicated host (BENCH_KILL_SCOPE=all) and reports them as strays elsewhere.
    `env` (if given) is overlaid on the inherited environment — used to point the CLI-first
    agent-device tool at the run's clone (AGENT_DEVICE_CONFIG) and put its shim on PATH.
    Returns (rc, stderr_tail, timed_out)."""
    p = subprocess.Popen(cmd, cwd=cwd, stdout=stdout_file, stderr=subprocess.PIPE,
                         text=True, start_new_session=True,
                         env=({**os.environ, **env} if env else None))
    register_pid(p.pid, "opencode-run")
    try:
        _, err = p.communicate(timeout=timeout)
        return p.returncode, (err or "")[-2000:], False
    except subprocess.TimeoutExpired:
        for sig, wait_s in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
            try:
                os.killpg(os.getpgid(p.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                p.communicate(timeout=wait_s)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            p.communicate(timeout=1)   # reap the child + close its stderr pipe even if killpg missed
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return -1, "TIMEOUT", True


# ---------------------------------------------------------------- server-reset hooks

class ResetError(RuntimeError):
    """A server-reset hook failed and the app's tasks are mutating — the run must not proceed."""


def run_reset_hook(cmd, outdir, timeout=300, hard_fail=True, label="reset"):
    """Run a server-side reset/seed/cleanup script; append stdout+stderr to reset-server.log in
    the run dir. hard_fail: nonzero exit raises ResetError, which PROPAGATES and aborts the matrix
    (a failed reset means the next run would be polluted — pollution IS the failure; the unit
    itself wrote no meta, so it stays pending). BENCH_RESET_SOFT=1 downgrades to a loud warning
    for debugging only."""
    hard_fail = hard_fail and os.environ.get("BENCH_RESET_SOFT") != "1"
    t0 = time.time()
    logp = os.path.join(outdir, "reset-server.log")
    with open(logp, "a") as log:
        log.write(f"\n=== {label}: {' '.join(cmd)} @ {time.strftime('%H:%M:%S')} ===\n")
        log.flush()
        try:
            r = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            rc = r.returncode
        except subprocess.TimeoutExpired:
            rc = -9
            log.write(f"=== {label}: TIMEOUT after {timeout}s ===\n")
    rep = {"cmd": cmd, "rc": rc, "duration_s": round(time.time() - t0, 1), "log": "reset-server.log"}
    if rc != 0:
        msg = f"server reset '{label}' failed rc={rc} (see {logp})"
        if hard_fail:
            raise ResetError(msg)
        print(f"  !! RESET FAILED (soft): {msg}", flush=True)
    return rep


# ---------------------------------------------------------------- env manifest

def write_env_manifest(outdir, **fields):
    """Per-run audit manifest -> <outdir>/env.json. ledger/report/judge address the four known
    result filenames explicitly, so extra files here are invisible to scoring."""
    path = os.path.join(outdir, "env.json")
    doc = {"schema": 1, **fields}
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, default=str)
        f.write("\n")
    return path
