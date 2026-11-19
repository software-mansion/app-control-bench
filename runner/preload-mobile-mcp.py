#!/usr/bin/env python3
"""Mobile MCP's host preload (runner/preload.py): the first launch of its `mobilecli` binary.

    python3 preload-mobile-mcp.py build <mobilecli binary>   launch it, then record that it ran
    python3 preload-mobile-mcp.py check <mobilecli binary>   exit 0 iff it ran for this install since boot

Mobile MCP does all of its device work by running `mobilecli`, a native binary that arrives with the npm
install. The first exec of a freshly installed binary pays macOS's one-time launch checks, which would
otherwise land inside whichever run goes first. `mobilecli --version` runs the binary and touches
nothing else: no device, and not the daemon the other subcommands start.

That first-launch state lives in the OS, where no check can read it, so the check reads the record this
script writes after the launch behaved as expected: keyed on the binary's identity and the boot, since a
reboot may drop what the OS kept. Same shape as preload-argent.py.

Not preloaded, because it is per device: the agent app (devicekit-ios) mobilecli installs on a simulator
the first time a tool targets it. See docs/tools.md.
"""
import hashlib, json, os, subprocess, sys

RECORDS = os.path.expanduser("~/.cache/bench-preload/mobile-mcp")


def die(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


if len(sys.argv) != 3 or sys.argv[1] not in ("build", "check"):
    die("usage: preload-mobile-mcp.py build|check <mobilecli binary>")
mode, binary = sys.argv[1], os.path.realpath(sys.argv[2])
try:
    st = os.stat(binary)
except OSError as e:
    die(f"mobilecli binary missing ({e}). It installs with @mobilenext/mobile-mcp; set BENCH_MOBILE_MCP_DIR "
        f"or MOBILECLI_PATH if it lives elsewhere.")
boot = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout.strip()
ident = {"binary": {"path": binary, "ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns}, "boot": boot}
record = os.path.join(RECORDS, hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:16])

if mode == "build":
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        die(f"{binary} --version: {e}", 1)
    out = r.stdout + r.stderr
    if r.returncode != 0 or not out.strip().startswith("mobilecli version "):
        die(f"{binary} --version: unexpected rc={r.returncode}: {out.strip()[-2000:]}", 1)
    os.makedirs(RECORDS, exist_ok=True)
    with open(record, "w") as f:
        json.dump(ident, f)
    print(f"launched {out.strip()} -> {record}")
    sys.exit(0)

ok = os.path.exists(record)
print(f"HIT {record}" if ok else f"MISS {record}: this install's mobilecli not launched by the preload since boot")
sys.exit(0 if ok else 1)
