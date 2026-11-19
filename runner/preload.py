#!/usr/bin/env python3
"""Host-level tool preload: the one-time work a tool does on the HOST (compiling a runner, filling a
cache, a binary's first launch) happens before a run's timer instead of inside whichever run of that
tool happens to go first.

The rule is the benchmark's, not the tools':
  - one-time work on the host that touches no device, app or session MAY happen before the timer;
  - anything that touches the run's device, app or session stays inside the timed window.

Every tool declares its preload in PRELOAD, and every entry goes through the same ensure(). An entry
with steps must carry a `check` - a command that exits 0 only when the artifact the steps produce is
present - so a preload can never pass without its artifact. A tool with nothing to preload has an
explicit empty entry whose `why` records how that was established. A tool with no entry is an error.

    python3 runner/preload.py                  # preload every tool in bench.TOOLS
    python3 runner/preload.py agent-device     # one tool
"""
import os, sys, time, string, subprocess
import bench_env, sim_device

# argv placeholders, resolved when an argv naming them runs, so env overrides (BENCH_AGENT_DEVICE, ...) apply.
PLACEHOLDERS = {
    "runner": lambda: os.path.dirname(os.path.abspath(__file__)),
    "python": lambda: sys.executable,
    "node": bench_env.node,
    "agent_device_dir": bench_env.agent_device_dir,
    "build_sim": sim_device.build_destination,
    "argent_pkg": bench_env.argent_pkg_dir,
    "mobilecli": lambda: bench_env.mobilecli() or "mobilecli-not-installed",
}

PRELOAD = {
    "agent-device": {
        "why": "Its XCTest runner: the first session on a host runs `xcodebuild build-for-testing` into "
               "~/.agent-device/apple-runner/derived/, keyed on the runner source, its build settings and "
               "the Xcode/SDK versions but on no device. Installing and launching the runner on the device "
               "stays timed.",
        "steps": [{"argv": ["{node}", "{runner}/preload-agent-device.mjs", "build", "{agent_device_dir}",
                            "{build_sim}"], "timeout": 1800}],
        "check": ["{node}", "{runner}/preload-agent-device.mjs", "check", "{agent_device_dir}"],
    },
    "argent": {
        "why": "The first launch of the install's native binaries, which pays macOS's one-time launch checks "
               "(measured at 0.15.0: simulator-server ~0.2 s, ax-service ~0.1 s; 0.34.0 launches the same two) inside whichever run goes "
               "first. Launched device-free by runner/preload-argent.py; the tool-server is not, since it "
               "reconfigures every booted simulator at startup. ~/.argent is left alone: its "
               "update-suppression.json decides whether the model sees the update notice, and the rest of "
               "it costs an MCP run nothing measurable. The dylibs' first load happens inside the simulator "
               "and stays timed.",
        "steps": [{"argv": ["{python}", "{runner}/preload-argent.py", "build", "{argent_pkg}"], "timeout": 120}],
        "check": ["{python}", "{runner}/preload-argent.py", "check", "{argent_pkg}"],
    },
    "mobile-mcp": {
        "why": "The first launch of `mobilecli`, the native binary Mobile MCP runs for every device action, "
               "which pays macOS's one-time launch checks inside whichever run goes first. Launched "
               "device-free with `--version` by runner/preload-mobile-mcp.py, which starts no daemon. The "
               "agent app mobilecli installs on a simulator is per device and stays timed, unless the "
               "golden carries it (docs/surfaces/golden-simulator.md).",
        "steps": [{"argv": ["{python}", "{runner}/preload-mobile-mcp.py", "build", "{mobilecli}"], "timeout": 120}],
        "check": ["{python}", "{runner}/preload-mobile-mcp.py", "check", "{mobilecli}"],
    },
    "none": {
        "why": "No tool: the baseline drives the device with the host's own Xcode toolchain, which the "
               "machine has before any run.",
        "steps": [],
        "check": None,
    },
}

TAIL_LINES = 40


class PreloadError(RuntimeError):
    """A tool's preload did not leave its artifact in place. Fatal for that tool's runs: none of them
    may start, and since nothing has been written yet their units stay pending."""


_built = set()      # variants whose steps ran in this process
_failed = {}        # variant -> the PreloadError message it failed with


def _expand(argv):
    names = {f for a in argv for _, f, _, _ in string.Formatter().parse(a) if f}
    vals = {k: PLACEHOLDERS[k]() for k in names}
    return [a.format(**vals) for a in argv]


def _run(argv, timeout):
    """(rc, seconds, output tail). A step that cannot start or overruns reports rc -9 rather than raising."""
    t0 = time.time()
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        # TimeoutExpired carries bytes even under text=True
        partial = b"".join(x if isinstance(x, bytes) else (x or "").encode() for x in (e.stdout, e.stderr))
        rc, out = -9, f"{partial.decode(errors='replace')}\nTIMEOUT after {timeout}s"
    except OSError as e:
        rc, out = -9, str(e)
    return rc, round(time.time() - t0, 1), "\n".join(out.rstrip().splitlines()[-TAIL_LINES:])


def _fail(variant, msg):
    _failed[variant] = msg
    raise PreloadError(msg)


def ensure(variant):
    """Make `variant`'s host artifact present before its timed run. Returns the audit record
    {"state": "built" | "present" | "none", "s": seconds, ...}.

    The check runs on every call; the steps run at most once per variant per process, and only when
    the check fails. A failure is remembered, so every later call for that variant raises at once."""
    if variant in _failed:
        raise PreloadError(_failed[variant])
    tool = variant.partition("@")[0]
    spec = PRELOAD.get(tool)
    if spec is None:
        _fail(variant, f"{tool}: no PRELOAD entry - declare one in runner/preload.py (empty if the tool "
                       f"has nothing to preload, with the evidence in `why`)")
    if not spec["steps"]:
        return {"state": "none", "s": 0.0}
    if not spec.get("check"):
        _fail(variant, f"{tool}: PRELOAD entry has steps but no check, so it could pass without its artifact")
    check = _expand(spec["check"])
    t0 = time.time()
    rc, _, _ = _run(check, 60)
    if rc == 0:
        return {"state": "present", "s": round(time.time() - t0, 1), "check": check}
    if variant in _built:
        _fail(variant, f"{variant}: preload artifact vanished after it was built in this process "
                       f"(check {check} rc={rc})")
    _built.add(variant)
    print(f"  PRELOAD {variant}: building host artifact before its first timed run", flush=True)
    steps = []
    for step in spec["steps"]:
        try:
            argv = _expand(step["argv"])
        except sim_device.DeviceError as e:
            _fail(variant, f"{variant}: preload step {step['argv']}: {e}")
        rc, s, tail = _run(argv, step["timeout"])
        steps.append({"argv": argv, "rc": rc, "s": s})
        if rc != 0:
            _fail(variant, f"{variant}: preload step {argv} failed rc={rc} after {s}s\n{tail}")
    rc, _, tail = _run(check, 60)
    if rc != 0:
        _fail(variant, f"{variant}: preload steps succeeded but the check {check} still fails rc={rc}\n{tail}")
    s = round(time.time() - t0, 1)
    print(f"  PRELOAD {variant}: built in {s}s", flush=True)
    return {"state": "built", "s": s, "check": check, "steps": steps}


def main(argv):
    import bench   # not at the top: bench imports this module
    tools = argv or bench.TOOLS
    failed = 0
    for tool in tools:
        variant = bench.tool_variant(tool)
        try:
            rec = ensure(variant)
            print(f"{variant}: {rec['state']} ({rec['s']}s)", flush=True)
        except PreloadError as e:
            failed += 1
            print(f"{variant}: FAILED\n{e}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
