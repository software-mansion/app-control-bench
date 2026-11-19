#!/usr/bin/env python3
"""argent's host preload (runner/preload.py): the first launch of the install's native binaries.

    python3 preload-argent.py build <argent package dir>   launch them, then record that they ran
    python3 preload-argent.py check <argent package dir>   exit 0 iff they ran for this install since boot

The first exec of a freshly installed binary pays macOS's one-time launch checks. An iOS run launches two:
simulator-server on the host, launched here with `fingerprint`, which hashes host identifiers and touches
no simulator; and ax-service inside the simulator, which dyld refuses to start on the host ("DYLD_ROOT_PATH
not set for simulator program") after the checks have run, so nothing of it executes. Host dlopen of the
dylibs fails at once, even the first time, so their first load stays in the run with the device work.

That first-launch state lives in the OS, where no check can read it, so the check reads the record this
script writes after both launches behaved as expected: keyed on the binaries' identity and the boot, since
a reboot may drop what the OS kept. The tool-server is not launched: at startup it rewrites the launchd
environment and accessibility defaults of every booted simulator.
"""
import hashlib, json, os, re, subprocess, sys

BINARIES = (
    ("bin/darwin/simulator-server", ["fingerprint"],
     lambda rc, out: rc == 0 and re.fullmatch(r"[0-9a-f]{64}", out.strip())),
    ("bin/darwin/ax-service", ["--help"],
     lambda rc, out: rc < 0 and "DYLD_ROOT_PATH not set for simulator program" in out),
)
RECORDS = os.path.expanduser("~/.cache/bench-preload/argent")


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def identity(pkg):
    """The binaries and the boot, as one key: a reinstall, an update or a reboot changes it."""
    bins = []
    for rel, _, _ in BINARIES:
        path = os.path.realpath(os.path.join(pkg, rel))
        try:
            st = os.stat(path)
        except OSError as e:
            die(f"argent package at {pkg}: {rel} missing ({e})")
        bins.append({"path": path, "ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    boot = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout.strip()
    ident = {"binaries": bins, "boot": boot}
    return ident, os.path.join(RECORDS, hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:16])


if len(sys.argv) != 3 or sys.argv[1] not in ("build", "check"):
    die("usage: preload-argent.py build|check <argent package dir>")
mode, pkg = sys.argv[1], sys.argv[2]
ident, record = identity(pkg)

if mode == "build":
    for (rel, args, expected), b in zip(BINARIES, ident["binaries"]):
        try:
            r = subprocess.run([b["path"], *args], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            die(f"{b['path']} {' '.join(args)}: {e}", 1)
        out = r.stdout + r.stderr
        if not expected(r.returncode, out):
            die(f"{b['path']} {' '.join(args)}: unexpected rc={r.returncode}: {out.strip()[-2000:]}", 1)
    os.makedirs(RECORDS, exist_ok=True)
    with open(record, "w") as f:
        json.dump(ident, f)
    print(f"launched {', '.join(rel for rel, _, _ in BINARIES)} -> {record}")
    sys.exit(0)

ok = os.path.exists(record)
print(f"HIT {record}" if ok else f"MISS {record}: this install's binaries not launched by the preload since boot")
sys.exit(0 if ok else 1)
