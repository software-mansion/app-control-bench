#!/usr/bin/env python3
"""Golden-template simulator lifecycle: every run gets a byte-identical fresh CLONE of a
permanently-shutdown 'golden' sim (apps installed + logged in + agent-device XCTest runner
pre-installed), and the clone NEVER outlives its run.

Why clone (not erase/reinstall): `simctl clone` copies app bundles, data containers and the
keychain, so the manually-established logins (real bsky.social account, Element alice, IceCubes)
are frozen into the golden — erase would destroy them and they are not re-automatable. Same
mechanism Xcode parallel testing uses; on APFS the copy is CoW-fast. The golden is resolved by
NAME from configs/golden.json (UDIDs differ per machine) and must always be Shutdown; the only
sanctioned boot of the golden itself is refresh_golden_sessions() (Bluesky refresh-token
rotation maintenance — see docs/surfaces/golden-simulator.md).

Stdlib + xcrun simctl only.
"""
import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_MANIFEST = os.path.join(ROOT, "configs", "golden.json")
GOLDEN_STATE = os.path.join(ROOT, "configs", "golden-state.json")   # per-host, gitignored: mutable stamps
CLONE_PREFIX = "bench-run-"          # reaping key: anything with this name prefix is ours to delete
BOOT_TIMEOUT = 240
CLONE_TIMEOUT = 120
SHUTDOWN_TIMEOUT = 60
BSKY_BUNDLE = "xyz.blueskyweb.app"
SESSION_REFRESH_MAX_AGE = int(os.environ.get("BENCH_GOLDEN_REFRESH_S", "3600"))


class DeviceError(RuntimeError):
    """Any golden/clone lifecycle failure — clone/boot callers map it to the SURFACE_ERROR rc=-2
    path (the unit stays pending); golden/reap failures propagate and abort the matrix."""


def _simctl(*args, timeout=60):
    """One simctl call. A hang is converted to DeviceError so every caller's error path stays in
    the module's single exception type (a raw TimeoutExpired would bypass SURFACE_ERROR handling)."""
    try:
        return subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DeviceError(f"simctl {args[0]} timed out after {timeout}s ({' '.join(args[:3])})")


def _devices():
    """Flat list of {name, udid, state, runtime} from `simctl list devices -j` (available only)."""
    r = _simctl("list", "devices", "-j")
    if r.returncode != 0:
        raise DeviceError(f"simctl list failed: {r.stderr.strip()[:200]}")
    out = []
    for runtime, devs in json.loads(r.stdout)["devices"].items():
        for d in devs:
            if d.get("isAvailable", True):
                out.append({"name": d["name"], "udid": d["udid"], "state": d["state"], "runtime": runtime})
    return out


def load_manifest():
    try:
        with open(GOLDEN_MANIFEST) as f:
            return json.load(f)
    except FileNotFoundError:
        raise DeviceError(f"no golden manifest at {GOLDEN_MANIFEST} — create the golden first "
                          f"(docs/surfaces/golden-simulator.md) and record it there")


def load_state():
    """Per-host mutable stamps (session_refreshed_ts, ...) — kept OUT of the tracked manifest so
    the hourly session refresh never dirties the git tree."""
    try:
        with open(GOLDEN_STATE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def save_state(**updates):
    s = {**load_state(), **updates}
    with open(GOLDEN_STATE, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    return s


def golden_info():
    """The golden device record (by manifest name) or DeviceError if missing."""
    name = load_manifest()["name"]
    for d in _devices():
        if d["name"] == name:
            return d
    raise DeviceError(f"golden simulator '{name}' not found on this machine — build it per "
                      f"docs/surfaces/golden-simulator.md")


def check_golden():
    """The golden must exist and be Shutdown. A booted golden is no longer trusted byte-state
    (someone ran on it) — abort rather than clone a polluted template."""
    g = golden_info()
    if g["state"] != "Shutdown":
        raise DeviceError(f"golden '{g['name']}' is {g['state']}, expected Shutdown — it is no "
                          f"longer a trusted template; re-golden (docs/surfaces/golden-simulator.md)")
    return g


def reap_stale_clones():
    """Delete any bench-run-* clones left by a crashed invocation, and enforce ONE-device-at-a-
    time: abort if a sim we don't own is Booted (never kill foreign devices — on a shared machine
    they belong to someone else; override with BENCH_IGNORE_FOREIGN_SIMS=1). A clone that survives
    destroy() is FATAL, same standard as an unkillable process — otherwise a wedged Booted clone
    would silently coexist with every later run's device, forever."""
    reaped, stuck, foreign = [], [], []
    for d in _devices():
        if d["name"].startswith(CLONE_PREFIX):
            (reaped if destroy(d["udid"]) else stuck).append(d["name"])
        elif d["state"] == "Booted":
            foreign.append(f"{d['name']} ({d['udid']})")
    if stuck:
        raise DeviceError(f"stale clone(s) survived shutdown+delete: {stuck} — CoreSimulator is "
                          f"wedged; fix it (killall -9 com.apple.CoreSimulator.CoreSimulatorService) "
                          f"before running")
    if foreign and os.environ.get("BENCH_IGNORE_FOREIGN_SIMS") != "1":
        raise DeviceError("foreign booted simulator(s) present — ONE device at a time: "
                          + ", ".join(foreign) + "  (shut them down, or BENCH_IGNORE_FOREIGN_SIMS=1)")
    return reaped


def clone_golden(task_id):
    """Clone the golden -> bench-run-<ts>-<task_id>. Same device set (same APFS volume -> CoW).
    Returns the clone's udid."""
    g = check_golden()
    name = f"{CLONE_PREFIX}{int(time.time())}-{task_id}"
    r = _simctl("clone", g["udid"], name, timeout=CLONE_TIMEOUT)
    if r.returncode != 0:
        raise DeviceError(f"clone of '{g['name']}' failed: {r.stderr.strip()[:200]}")
    lines = r.stdout.strip().splitlines()
    udid = lines[-1].strip() if lines else ""
    if len(udid) != 36:   # simctl prints the new udid on stdout; be defensive (incl. empty stdout)
        found = [d for d in _devices() if d["name"] == name]
        if not found:
            raise DeviceError(f"clone '{name}' created but udid not resolvable")
        udid = found[0]["udid"]
    return udid


def boot(udid):
    """Boot headless and BLOCK until fully booted (bootstatus -b), no fixed sleeps."""
    r = _simctl("boot", udid, timeout=60)
    if r.returncode != 0 and "current state: Booted" not in (r.stderr or ""):
        raise DeviceError(f"boot {udid} failed: {r.stderr.strip()[:200]}")
    r = _simctl("bootstatus", udid, "-b", timeout=BOOT_TIMEOUT)
    if r.returncode != 0:
        raise DeviceError(f"bootstatus {udid} failed: {r.stderr.strip()[:200]}")


def destroy(udid):
    """Shutdown + delete a clone. Best-effort with retries; runs in finally — logs, NEVER raises
    (the caller decides whether a survivor is fatal). Returns True iff the device is gone."""
    try:
        for _ in range(2):
            try:
                r = _simctl("shutdown", udid, timeout=SHUTDOWN_TIMEOUT)
            except DeviceError:
                continue   # hung — retry once, delete may still work
            # rc!=0 with "current state: Shutdown" just means already down; anything else retries
            if r.returncode == 0 or "current state: Shutdown" in (r.stderr or ""):
                break
        for _ in range(2):
            try:
                r = _simctl("delete", udid, timeout=SHUTDOWN_TIMEOUT)
                if r.returncode == 0:
                    return True
            except DeviceError:
                pass
            time.sleep(2)
        gone = not any(d["udid"] == udid for d in _devices())
    except DeviceError:
        gone = False   # even the existence check failed — CoreSimulator is unhealthy
    if not gone:
        print(f"  !! clone {udid} could not be deleted — the next run's reap treats this as fatal",
              flush=True)
    return gone


def refresh_golden_sessions(force=False):
    """Bluesky AT-proto refresh-token rotation maintenance: the golden freezes one refreshJwt, and
    clones refreshing it outside the server's reuse-grace window get logged out. Periodically boot
    the GOLDEN itself, let Bluesky refresh+persist a fresh token, shutdown, stamp the state file
    (per-host, gitignored — never dirties the tree). This is the only sanctioned boot of the
    golden. Callers hold the device lock and run this while no clone exists."""
    age = time.time() - (load_state().get("session_refreshed_ts") or 0)
    if not force and age < SESSION_REFRESH_MAX_AGE:
        return False
    g = check_golden()
    print(f"  golden session refresh ({g['name']}, last {int(age / 60)}min ago)...", flush=True)
    try:
        boot(g["udid"])
        r = _simctl("launch", g["udid"], BSKY_BUNDLE, timeout=60)
        if r.returncode != 0:
            raise DeviceError(f"bluesky launch on golden failed: {r.stderr.strip()[:200]}")
        time.sleep(20)   # app refreshes + persists the session on foreground
        _simctl("terminate", g["udid"], BSKY_BUNDLE, timeout=30)
    finally:
        for _ in range(2):
            try:
                r = _simctl("shutdown", g["udid"], timeout=SHUTDOWN_TIMEOUT)
                if r.returncode == 0 or "current state: Shutdown" in (r.stderr or ""):
                    break
            except DeviceError:
                continue
    if golden_info()["state"] != "Shutdown":
        raise DeviceError(f"golden '{g['name']}' failed to shut down after session refresh")
    save_state(session_refreshed_ts=int(time.time()))
    print("  golden session refresh done", flush=True)
    return True
