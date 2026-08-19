#!/usr/bin/env python3
"""Golden-template simulator lifecycle: every run gets a byte-identical fresh CLONE of a
permanently-shutdown 'golden' sim (apps installed + logged in + agent-device XCTest runner
pre-installed). Every clone created by this Python invocation is shut down and deleted in its
caller's finally block. Creation intent is durably journaled first, so the next locked invocation
can retire a clone left by a killed process; names and prefixes alone never authorize deletion.

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
import tempfile
import time
import uuid

import bench_env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_MANIFEST = os.path.join(ROOT, "configs", "golden.json")
GOLDEN_STATE = os.path.join(ROOT, "configs", "golden-state.json")   # per-host, gitignored: mutable stamps
CLONE_PREFIX = os.environ.get("BENCH_CLONE_PREFIX", "bench-run-")
BOOT_TIMEOUT = 240
CLONE_TIMEOUT = 120
SHUTDOWN_TIMEOUT = 60
BSKY_BUNDLE = "xyz.blueskyweb.app"
SESSION_REFRESH_MAX_AGE = int(os.environ.get("BENCH_GOLDEN_REFRESH_S", "3600"))
OWNERSHIP_FILE = bench_env.simulator_ownership_file()


class DeviceError(RuntimeError):
    """Any golden/clone lifecycle failure — clone/boot callers map it to the SURFACE_ERROR rc=-2
    path (the unit stays pending); golden/reap failures propagate and abort the matrix."""


def _load_owned_clones():
    try:
        with open(OWNERSHIP_FILE) as stream:
            document = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise DeviceError(f"simulator ownership journal is unreadable: {error}") from error
    if (
        not isinstance(document, dict)
        or document.get("schema") != 1
        or not isinstance(document.get("clones"), dict)
    ):
        raise DeviceError("simulator ownership journal has an unsupported schema")
    clones = document["clones"]
    if any(
        not isinstance(name, str)
        or not isinstance(record, dict)
        or record.get("name") != name
        or (record.get("udid") is not None and not isinstance(record.get("udid"), str))
        for name, record in clones.items()
    ):
        raise DeviceError("simulator ownership journal contains an invalid clone record")
    return clones


def _save_owned_clones(clones):
    directory = os.path.dirname(OWNERSHIP_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="owned-clones-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"schema": 1, "clones": clones}, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, OWNERSHIP_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record_clone(name, udid=None):
    clones = _load_owned_clones()
    clones[name] = {
        "name": name,
        "udid": udid,
        "created_ts": clones.get(name, {}).get("created_ts", int(time.time())),
    }
    _save_owned_clones(clones)


def _forget_clone(name):
    clones = _load_owned_clones()
    if clones.pop(name, None) is not None:
        _save_owned_clones(clones)


def _resolve_recorded_clone(name):
    """Resolve one exact journaled random name to a UDID, never a prefix or caller-supplied name."""
    matches = [device for device in _devices() if device["name"] == name]
    if len(matches) > 1:
        raise DeviceError(f"journaled clone name '{name}' is ambiguous; refusing cleanup")
    if not matches:
        return None
    udid = matches[0]["udid"]
    _record_clone(name, udid)
    return udid


def _simctl(*args, timeout=60):
    """One simctl call. A hang is converted to DeviceError so every caller's error path stays in
    the module's single exception type (a raw TimeoutExpired would bypass SURFACE_ERROR handling)."""
    try:
        return subprocess.run(
            ["xcrun", "simctl", "--set", bench_env.device_set(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
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


def require_bluesky_backend_identity(identity):
    """Reject a backend restart that invalidates the golden's persisted app token."""
    previous = load_state().get("bluesky_backend_identity")
    if previous and previous != identity:
        raise DeviceError(
            "Bluesky backend identity changed since the golden was clone-verified; log the golden "
            "into the current backend and verify a fresh clone before running paid cases"
        )
    return previous


def confirm_bluesky_clone_session(identity, *, clone_authenticated, refreshed):
    """Persist identity/refresh stamps only after the actual fresh clone passed UI validation."""
    if not clone_authenticated:
        raise DeviceError("cannot stamp Bluesky session state without an authenticated clone")
    now = int(time.time())
    updates = {
        "bluesky_backend_identity": identity,
        "clone_session_verified_ts": now,
    }
    if refreshed:
        updates["session_refreshed_ts"] = now
    return save_state(**updates)


def golden_info():
    """The golden device record (by manifest name) or DeviceError if missing."""
    name = load_manifest()["name"]
    matches = [d for d in _devices() if d["name"] == name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        identities = ", ".join(f"{d['udid']} ({d['runtime']})" for d in matches)
        raise DeviceError(
            f"golden simulator name '{name}' is ambiguous: {identities} — rename or remove stale "
            "duplicates before running the benchmark"
        )
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


def check_device_conflicts():
    """Observe pre-existing booted simulators without mutating or rejecting them.

    Every benchmark command is scoped to its clone UDID, while the benchmark lock prevents two
    harness streams from racing. Other booted simulators can affect host load, so callers record
    them for timing interpretation, but they are not a correctness failure.
    """
    foreign = []
    for d in _devices():
        if d["state"] == "Booted":
            foreign.append(f"{d['name']} ({d['udid']})")
    return foreign


def clone_golden(task_id):
    """Clone the golden -> bench-run-<ts>-<task_id>. Same device set (same APFS volume -> CoW).
    Returns the clone's udid."""
    g = check_golden()
    name = f"{CLONE_PREFIX}{int(time.time())}-{task_id}-{uuid.uuid4().hex}"
    # Persist the unguessable exact name before simctl runs. If this process is killed after clone
    # creation but before receiving its UDID, the next locked invocation can still retire only the
    # simulator this harness intended to create.
    _record_clone(name)
    try:
        r = _simctl("clone", g["udid"], name, timeout=CLONE_TIMEOUT)
    except DeviceError as error:
        # CoreSimulatorService can finish work after the direct simctl process times out. Reconcile
        # an already-visible exact-name clone immediately; otherwise leave the durable unbound
        # intent in place so the next run fails closed until delayed materialization is resolved.
        created_udid = _resolve_recorded_clone(name)
        if created_udid is not None and not destroy(created_udid):
            raise DeviceError(f"timed-out clone '{name}' could not be retired") from error
        raise
    if r.returncode != 0:
        # A failed simctl may still have materialized the requested clone. Resolve and retire the
        # exact 128-bit-random journaled name before returning failure; otherwise retain the intent
        # so a later invocation can catch delayed CoreSimulator materialization.
        created_udid = _resolve_recorded_clone(name)
        if created_udid is not None and not destroy(created_udid):
            raise DeviceError(f"failed clone '{name}' could not be retired")
        raise DeviceError(f"clone of '{g['name']}' failed: {r.stderr.strip()[:200]}")
    lines = r.stdout.strip().splitlines()
    udid = lines[-1].strip() if lines else ""
    if len(udid) != 36:   # simctl prints the new udid on stdout; be defensive (incl. empty stdout)
        udid = _resolve_recorded_clone(name)
        if udid is None:
            raise DeviceError(f"clone '{name}' created but udid not resolvable")
    _record_clone(name, udid)
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
    """Shut down and delete one clone recorded in the durable ownership journal.

    Names and prefixes never authorize deletion. A caller-supplied UDID, a pre-existing simulator,
    and the golden are all refused without issuing simctl mutations.
    """
    owned = _load_owned_clones()
    records = [record for record in owned.values() if record.get("udid") == udid]
    if len(records) != 1:
        print(f"  !! refusing to retire unowned simulator {udid}", flush=True)
        return False
    record = records[0]
    current = next((device for device in _devices() if device["udid"] == udid), None)
    if current is None:
        _forget_clone(record["name"])
        return True
    if current["name"] != record["name"]:
        print(f"  !! refusing to retire simulator {udid}: ownership name mismatch", flush=True)
        return False
    try:
        try:
            golden_udid = golden_info()["udid"]
        except DeviceError:
            golden_udid = None
        if golden_udid == udid:
            print(f"  !! refusing to retire golden simulator {udid}", flush=True)
            return False
        for _ in range(2):
            try:
                r = _simctl("shutdown", udid, timeout=SHUTDOWN_TIMEOUT)
            except DeviceError:
                continue
            # rc!=0 with "current state: Shutdown" just means already down; anything else retries
            if r.returncode == 0 or "current state: Shutdown" in (r.stderr or ""):
                break
        deadline = time.time() + SHUTDOWN_TIMEOUT
        while time.time() < deadline:
            state = next((d["state"] for d in _devices() if d["udid"] == udid), None)
            if state == "Shutdown":
                break
            if state is None:
                _forget_clone(record["name"])
                return True
            time.sleep(1)
        deleted = _simctl("delete", udid, timeout=SHUTDOWN_TIMEOUT)
        if deleted.returncode != 0:
            print(f"  !! clone {udid} delete failed: {(deleted.stderr or '').strip()[:200]}", flush=True)
            return False
        if any(d["udid"] == udid for d in _devices()):
            print(f"  !! clone {udid} still exists after delete", flush=True)
            return False
        _forget_clone(record["name"])
        return True
    except DeviceError:
        pass
    print(f"  !! clone {udid} could not be shut down and deleted", flush=True)
    return False


def reap_owned_clones():
    """Retire only clones journaled by an earlier benchmark process.

    This repairs process-kill leaks without reviving the unsafe historical behavior that treated a
    name prefix as proof of ownership.
    """
    reaped = []
    for name, record in list(_load_owned_clones().items()):
        udid = record.get("udid")
        if not udid:
            # The parent can be killed while simctl keeps cloning. During the original clone timeout
            # window, wait for that child/service operation to materialize. Never discard an unbound
            # intent automatically: a late clone must remain discoverable on every future invocation.
            deadline = record.get("created_ts", 0) + CLONE_TIMEOUT + 5
            while True:
                udid = _resolve_recorded_clone(name)
                if udid is not None or time.time() >= deadline:
                    break
                time.sleep(1)
            if udid is None:
                raise DeviceError(
                    f"journaled clone intent '{name}' is still unresolved; refusing to create "
                    "another simulator until CoreSimulator reports the exact clone or an operator "
                    "verifies the intent is stale"
                )
        if not destroy(udid):
            raise DeviceError(f"journaled clone {name} ({udid}) could not be retired")
        reaped.append(name)
    return reaped


def refresh_golden_sessions(force=False):
    """Bluesky AT-proto refresh-token rotation maintenance: the golden freezes one refreshJwt, and
    clones refreshing it outside the server's reuse-grace window get logged out. Periodically boot
    the GOLDEN itself, let Bluesky refresh+persist a fresh token, and shut it down. This deliberately
    does NOT stamp success: the caller must validate the actual fresh clone and then call
    confirm_bluesky_clone_session(). This is the only sanctioned boot of the golden. Callers hold
    the device lock and run this while no clone exists."""
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
    print("  golden session refresh pending clone verification", flush=True)
    return True
