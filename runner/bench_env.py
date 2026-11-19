#!/usr/bin/env python3
"""Single source of truth for the machine-specific bits — tool binaries and the target simulator — so the
bench is portable and needs ZERO per-machine constant editing. Every value resolves in one order:

    explicit env override  ->  auto-detect on this machine  ->  documented fallback

Import-safe (no side effects at import). Used by bench.py, sim_device.py, gen_configs.py, doctor.py.

Env overrides:
  UDID / BENCH_UDID          target simulator udid (else the single booted sim is auto-picked)
  BENCH_NODE                 node binary            (else `which node`)
  BENCH_OPENCODE             opencode binary        (else ~/.opencode/bin/opencode, else `which`)
  BENCH_AGENT_DEVICE         agent-device.mjs path  (else ~/dev/agent-device/bin, else `which agent-device`)
  BENCH_AGENT_DEVICE_DIR     agent-device repo dir  (else the agent-device package holding the resolved binary)
  BENCH_MOBILE_MCP           mcp-server-mobile path (else `which mcp-server-mobile`)
  BENCH_MOBILE_MCP_DIR       @mobilenext/mobile-mcp package dir (else the package holding the resolved binary)
  BENCH_ARGENT               argent binary          (else `which argent`)
  BENCH_DEVICE_SET           CoreSimulator device-set dir (else the default user path)
"""
import os, re, shutil, subprocess, shlex, json

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

DEFAULT_DEVICE_SET = os.path.expanduser("~/Library/Developer/CoreSimulator/Devices")


def _package_root(binary, accept):
    """Nearest ancestor of `binary`'s realpath whose package.json name satisfies `accept`, else None."""
    d = os.path.dirname(os.path.realpath(binary))
    while d and d != "/":
        pj = os.path.join(d, "package.json")
        if os.path.exists(pj):
            try:
                with open(pj) as f:
                    if accept(json.load(f).get("name") or ""):
                        return d
            except Exception:
                pass
        d = os.path.dirname(d)
    return None


def _which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


# OpenCode's credentials. opencode 2 keeps them in its database: it imports a 1.x `auth.json` once, when
# the database is first created, and stores every later login there alone, so the file goes stale.
# Ask opencode itself (`opencode auth ...`), and fall back to the 1.x file for a host with no usable
# opencode 2. `--standalone` keeps the query from starting the host's shared background service.
AUTH_PATHS = ("~/.local/share/opencode/auth.json", "~/.opencode/auth.json")
_auth_cache = {}


def _opencode_json(*args):
    """Parsed JSON output of `opencode <args> --standalone`, or None if it cannot be had."""
    import subprocess, tempfile
    try:
        out = subprocess.run([opencode(), *args, "--standalone"], capture_output=True, text=True, timeout=60,
                             cwd=tempfile.gettempdir())
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _legacy_auth():
    for p in AUTH_PATHS:
        p = os.path.expanduser(p)
        try:
            with open(p) as f:
                return json.load(f), p
        except (OSError, ValueError):
            continue
    return {}, None


def auth_store():
    """(provider ids OpenCode holds a credential for, where that was read from). (set(), None) if there
    is no readable store. Never returns the keys themselves — callers only ever need presence. Cached
    for the process: the answer comes from a subprocess."""
    if "store" not in _auth_cache:
        listed = _opencode_json("auth", "list", "--format", "json")
        if isinstance(listed, list):
            ids = {e.get("id") for e in listed if isinstance(e, dict) and e.get("connections")}
            _auth_cache["store"] = (ids, "opencode auth list")
        else:
            legacy, path = _legacy_auth()
            _auth_cache["store"] = (set(legacy.keys()), path)
    return _auth_cache["store"]


def auth_providers():
    return auth_store()[0]


def auth_key(provider):
    """The API key OpenCode holds for `provider`, or None. For the judge, which calls the provider
    itself with the key the runs use."""
    for cred in _opencode_json("auth", "export") or []:
        value = cred.get("value") or {} if isinstance(cred, dict) else {}
        if cred.get("integrationID") == provider and cred.get("active", True) and value.get("key"):
            return value["key"]
    entry = _legacy_auth()[0].get(provider) or {}
    return entry.get("key") or entry.get("apiKey")


def node():
    return os.environ.get("BENCH_NODE") or _which("node") or "node"


def opencode():
    env = os.environ.get("BENCH_OPENCODE")
    if env:
        return env
    p = os.path.expanduser("~/.opencode/bin/opencode")
    return p if os.path.exists(p) else (_which("opencode") or p)


def agent_device():
    env = os.environ.get("BENCH_AGENT_DEVICE")
    if env:
        return env
    p = os.path.expanduser("~/dev/agent-device/bin/agent-device.mjs")
    if os.path.exists(p):
        return p
    return _which("agent-device") or p


def agent_device_dir():
    """The agent-device package (its version names the result cell; its skills/ are staged). Found
    through the binary's realpath, so a symlinked global npm bin resolves to the real package."""
    env = os.environ.get("BENCH_AGENT_DEVICE_DIR")
    if env:
        return env
    b = agent_device()
    return (_package_root(b, lambda name: name == "agent-device")
            or os.path.dirname(os.path.dirname(os.path.realpath(b))))


def agent_device_shim_dir():
    """A PATH dir holding an `agent-device` command, so an agent driving the CLI-first tool through a
    shell can type `agent-device ...` verbatim (the real ergonomics) regardless of how the binary is
    named/installed here. The wrapper pins the absolute node + .mjs so it works even when the shell
    opencode spawns has no node on PATH. Idempotent; returns the dir to prepend to PATH."""
    d = os.path.expanduser("~/.cache/bench-shims")
    os.makedirs(d, exist_ok=True)
    shim = os.path.join(d, "agent-device")
    body = f'#!/bin/sh\nexec {shlex.quote(node())} {shlex.quote(agent_device())} "$@"\n'
    if (not os.path.exists(shim)) or open(shim).read() != body:
        with open(shim, "w") as f:
            f.write(body)
        os.chmod(shim, 0o755)
    return d


def sandbox_wrap(cmd, udid):
    """Wrap the `none` baseline's agent in a sandbox that cannot destroy the simulator host.

    The PATH shims in none_shim_dir() scope `xcrun simctl` to the run's own clone, but a shim only
    shadows a *command name* - and the baseline agent has a full shell. Simulators are just
    directories owned by the same user, so `rm`, `mv`, `python` walk straight around it. On
    2026-07-13 a none agent did exactly that: it reached into ANOTHER device's app container with
    `rm -rf .../Devices/<other-udid>/data/Containers/...`, and between that and the crash handler's
    `killall -9 CoreSimulatorService` **every simulator on the host was destroyed**, both goldens
    included (docs/surfaces/golden-simulator.md). Nothing in the shim layer can prevent that; only a
    kernel-enforced boundary can.

    So: deny the agent WRITES to the device set (except its own clone) and to the backups that are
    the recovery path. Everything else is untouched - the baseline's advertised surface is unchanged,
    it still gets a shell, xcrun, xcodebuild and simctl against its own device.

    `simctl` keeps working on the clone because the writes are performed by CoreSimulatorService over
    XPC, not by the agent process, and the service is outside this sandbox.

    Reads stay allowed on purpose: the point is to stop destruction, not to hide the host. And
    `results/` is deliberately NOT denied - the agent's stdout IS the run transcript, which lives
    there, so denying it would break the harness's own capture.

    No-ops (returns cmd unchanged) if sandbox-exec is missing, so a non-macOS host still runs.
    """
    if not os.path.exists(SANDBOX_EXEC):
        return cmd
    # realpath, always: the sandbox matches CANONICAL paths, so a subpath naming a symlinked parent
    # (/var -> /private/var is the classic) silently matches nothing and the rule is a no-op.
    devices = os.path.realpath(device_set())
    own = os.path.join(devices, udid) if udid else None
    rules = ['(version 1)', '(allow default)',
             '(deny file-write* (subpath %s))' % json.dumps(devices)]
    if own:                                   # last match wins in SBPL: re-allow just this clone
        rules.append('(allow file-write* (subpath %s))' % json.dumps(own))
    for keep in ("~/golden-backups", "~/apps"):   # the only copies of the goldens and the app bundle
        p = os.path.expanduser(keep)
        if os.path.isdir(p):
            rules.append('(deny file-write* (subpath %s))' % json.dumps(os.path.realpath(p)))
    return [SANDBOX_EXEC, "-p", "\n".join(rules)] + list(cmd)


def none_shim_dir(udid=None):
    """A PATH dir of shims for the `none` baseline. Two jobs, both about keeping the baseline's reach
    equal to one device:

    1. BLOCK host-level GUI automation. Left to itself the bare no-tool agent bootstraps taps by driving
       the HOST mouse and keyboard (`osascript` -> System Events / CGEvent), which is both a side effect
       outside the device under test and too flaky to run unattended. Shadowing these on PATH makes them
       fail fast, so perception/action is confined to the device-scoped native tooling (simctl/xcrun/
       xctest) the preamble enumerates. The Python Quartz/CGEvent host-synth path is separately closed
       (no pyobjc installed).

    2. SCOPE `xcrun simctl` to this run's own clone (when `udid` is given). simctl is deliberately part of
       the baseline's surface, but it targets ANY device on the host, and `simctl list` shows the agent the
       golden by name. An agent that boots the golden and launches the app on it rotates the golden's frozen
       Bluesky refreshJwt out of band, and every later clone then starts LOGGED OUT — silently zeroing the
       rest of the matrix. It also trips the harness's own one-device-at-a-time guard, aborting run_matrix.
       Both happened (haiku_low/none/bsky-07 booted the golden; see docs/harness.md). The shim rejects any
       simctl arg naming a device other than this run's clone, and the device-lifecycle subcommands
       (create/clone/delete/erase/...) outright — none of which the preamble offers anyway, so the
       advertised baseline surface is unchanged. Everything else execs the real xcrun untouched.

    Idempotent; returns the dir to prepend to PATH."""
    d = os.path.expanduser("~/.cache/bench-shims-none")
    os.makedirs(d, exist_ok=True)
    body = ('#!/bin/sh\n'
            'echo "$(basename "$0"): host GUI automation is not available in this environment" >&2\n'
            'exit 127\n')
    for name in ("osascript", "osacompile", "automator", "cliclick"):
        shim = os.path.join(d, name)
        if (not os.path.exists(shim)) or open(shim).read() != body:
            with open(shim, "w") as f:
                f.write(body)
            os.chmod(shim, 0o755)

    xcrun_shim = os.path.join(d, "xcrun")
    if not udid:
        if os.path.exists(xcrun_shim):
            os.remove(xcrun_shim)     # no udid to pin: don't shadow xcrun at all
        return d
    # The clone is the ONLY device this run may touch. Pass through anything that is not `simctl`.
    guard = f'''#!/bin/sh
OWN={shlex.quote(udid)}
REAL=/usr/bin/xcrun
[ "$1" = simctl ] || exec "$REAL" "$@"
case "$2" in
  create|clone|delete|erase|upgrade|pair|unpair|pair_activate)
    echo "xcrun simctl $2: device lifecycle is managed by the harness, not the agent" >&2; exit 1 ;;
esac
for a in "$@"; do
  case "$a" in
    bench-golden*|bench-element*|bench-run-*)
      [ "$a" = "$OWN" ] && continue
      echo "xcrun simctl: '$a' is not your device — this run owns $OWN only" >&2; exit 1 ;;
  esac
  # a UUID-shaped argument that is not ours (case-insensitive)
  case "$a" in
    ????????-????-????-????-????????????)
      lower_a=$(printf '%s' "$a" | tr 'A-Z' 'a-z')
      lower_own=$(printf '%s' "$OWN" | tr 'A-Z' 'a-z')
      [ "$lower_a" = "$lower_own" ] && continue
      echo "xcrun simctl: device $a is not yours — this run owns $OWN only" >&2; exit 1 ;;
  esac
done
exec "$REAL" "$@"
'''
    if (not os.path.exists(xcrun_shim)) or open(xcrun_shim).read() != guard:
        with open(xcrun_shim, "w") as f:
            f.write(guard)
        os.chmod(xcrun_shim, 0o755)
    return d


def mobile_mcp():
    """Mobile MCP's stdio server (`npm install -g @mobilenext/mobile-mcp` puts it on PATH)."""
    return os.environ.get("BENCH_MOBILE_MCP") or _which("mcp-server-mobile") or "mcp-server-mobile"


def mobile_mcp_dir():
    """Root of the installed @mobilenext/mobile-mcp package: its version names the result cell. Found
    through the binary's realpath, like the other tools."""
    env = os.environ.get("BENCH_MOBILE_MCP_DIR")
    if env:
        return env
    return _package_root(_which(mobile_mcp()) or mobile_mcp(), lambda name: name == "@mobilenext/mobile-mcp")


def mobilecli():
    """The `mobilecli` binary Mobile MCP drives every device through. It ships as a per-platform npm
    package installed beside mobile-mcp, and the server finds it the same way this does: in the
    node_modules directory that holds mobile-mcp itself."""
    env = os.environ.get("MOBILECLI_PATH")
    if env:
        return env
    pkg = mobile_mcp_dir()
    if not pkg:
        return None
    arch = "arm64" if os.uname().machine == "arm64" else "amd64"
    name = f"mobilecli-darwin-{arch}"
    scope = os.path.dirname(pkg)                        # .../node_modules/@mobilenext
    for path in (os.path.join(scope, name, name), os.path.join(os.path.dirname(scope), "mobilecli", "bin", name)):
        if os.path.exists(path):
            return path
    return None


def argent():
    return os.environ.get("BENCH_ARGENT") or _which("argent") or "argent"


def argent_pkg_dir():
    """Root of the installed @swmansion/argent package — holds the skills/, rules/ and agents/ a real
    `argent init` copies into an agent config. Resolved from the argent binary (realpath through the
    nvm/npm symlink) by walking up to the package.json whose name contains 'argent'. This pins the
    skill/rule set to the SAME argent version whose `argent mcp` the benchmark drives (0.13.0 here),
    so a run never discloses skills that don't match its MCP server."""
    env = os.environ.get("BENCH_ARGENT_PKG")
    if env:
        return env
    return _package_root(_which(argent()) or argent(), lambda name: "argent" in name)


def argent_skills_dir():
    p = argent_pkg_dir()
    return os.path.join(p, "skills") if p else None


def argent_rule_file():
    p = argent_pkg_dir()
    return os.path.join(p, "rules", "argent.md") if p else None


def argent_agents_dir():
    p = argent_pkg_dir()
    return os.path.join(p, "agents") if p else None


def agent_device_skills_dir():
    """The SKILL.md router set agent-device ships (agent-device + dogfood). A real setup installs these
    via `npx skills add callstack/agent-device`; the bodies route to on-demand `agent-device help ...`."""
    return os.path.join(agent_device_dir(), "skills")


def device_set():
    return os.environ.get("BENCH_DEVICE_SET") or DEFAULT_DEVICE_SET


def booted_udids():
    """UDIDs of currently-booted simulators (empty on failure)."""
    try:
        r = subprocess.run(["xcrun", "simctl", "--set", device_set(), "list", "devices", "booted"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    return re.findall(r"\(([0-9A-Fa-f-]{36})\)\s*\(Booted\)", r.stdout)


def udid(fallback=None):
    """Target sim: explicit env wins; else the single booted sim; else the first booted; else `fallback`.
    Returns None (or fallback) when nothing is booted so callers can fail loudly instead of driving a ghost."""
    env = os.environ.get("UDID") or os.environ.get("BENCH_UDID")
    if env:
        return env
    b = booted_udids()
    if b:
        return b[0]
    return fallback


def resolve(fallback_udid=None):
    """Everything at once, for diagnostics."""
    return {
        "udid": udid(fallback_udid),
        "booted": booted_udids(),
        "node": node(),
        "opencode": opencode(),
        "agent_device": agent_device(),
        "agent_device_dir": agent_device_dir(),
        "argent": argent(),
        "mobile_mcp": mobile_mcp(),
        "device_set": device_set(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(resolve(), indent=2))
