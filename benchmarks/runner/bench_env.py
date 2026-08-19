#!/usr/bin/env python3
"""Single source of truth for the machine-specific bits — tool binaries and the target simulator — so the
bench is portable and needs ZERO per-machine constant editing. Every value resolves in one order:

    explicit env override  ->  auto-detect on this machine  ->  documented fallback

Import-safe (no side effects at import). Used by bench.py, run_matrix.py, gen_configs.py, doctor.py.

Env overrides:
  UDID / BENCH_UDID          target simulator udid (else the single booted sim is auto-picked)
  BENCH_NODE                 node binary            (else `which node`)
  BENCH_OPENCODE             opencode binary        (else ~/.opencode/bin/opencode, else `which`)
  BENCH_AGENT_DEVICE         agent-device.mjs path  (else ~/dev/agent-device/bin, else `which agent-device`)
  BENCH_AGENT_DEVICE_DIR     agent-device package/repo dir (else resolve from the CLI package.json)
  BENCH_AGENT_DEVICE_SKILLS  path-separated installed skill directories (else auto-detect)
  BENCH_ARGENT               argent binary          (else `which argent`)
  BENCH_DEVICE_SET           CoreSimulator device-set dir (else the default user path)
  BENCH_SIMULATOR_STATE_DIR  durable clone-ownership journal directory
"""
import os, re, shutil, subprocess, shlex, json, hashlib

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

DEFAULT_DEVICE_SET = os.path.expanduser("~/Library/Developer/CoreSimulator/Devices")


def simulator_state_dir():
    return os.path.realpath(
        os.environ.get(
            "BENCH_SIMULATOR_STATE_DIR",
            os.path.expanduser("~/.cache/app-control-bench/simulators"),
        )
    )


def simulator_ownership_file():
    device_set_key = hashlib.sha256(os.path.realpath(device_set()).encode()).hexdigest()[:12]
    return os.path.join(simulator_state_dir(), f"owned-clones-{device_set_key}.json")


def _which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def node():
    return os.environ.get("BENCH_NODE") or _which("node") or "node"


def opencode():
    env = os.environ.get("BENCH_OPENCODE")
    if env:
        return env
    p = os.path.expanduser("~/.opencode/bin/opencode")
    return p if os.path.exists(p) else (_which("opencode") or p)


def opencode_auth():
    for path in (os.path.expanduser("~/.local/share/opencode/auth.json"),
                 os.path.expanduser("~/.opencode/auth.json")):
        try:
            with open(path) as f:
                return json.load(f), path
        except Exception:
            continue
    return {}, None


def vercel_ai_gateway_key():
    key = os.environ.get("AI_GATEWAY_API_KEY")
    if key:
        return key
    auth, _ = opencode_auth()
    entry = auth.get("vercel") or {}
    return entry.get("key") or entry.get("apiKey")


def agent_device():
    env = os.environ.get("BENCH_AGENT_DEVICE")
    if env:
        return env
    p = os.path.expanduser("~/dev/agent-device/bin/agent-device.mjs")
    if os.path.exists(p):
        return p
    return _which("agent-device") or p


def agent_device_dir():
    env = os.environ.get("BENCH_AGENT_DEVICE_DIR")
    if env:
        return env
    # npm links <prefix>/bin/agent-device into <prefix>/lib/node_modules/agent-device/bin. Walking
    # parent-of-parent without resolving that link returns the Node prefix and silently loses both
    # package version provenance and skill discovery.
    d = os.path.dirname(os.path.realpath(agent_device()))
    while d and d != "/":
        pj = os.path.join(d, "package.json")
        try:
            if os.path.exists(pj):
                with open(pj) as f:
                    if json.load(f).get("name") == "agent-device":
                        return d
        except Exception:
            pass
        d = os.path.dirname(d)
    return os.path.dirname(os.path.dirname(os.path.realpath(agent_device())))


def agent_device_shim_dir(run_dir):
    """A PATH dir holding an `agent-device` command, so an agent driving the CLI-first tool through a
    shell can type `agent-device ...` verbatim (the real ergonomics) regardless of how the binary is
    named/installed here. The wrapper pins the absolute node + .mjs so it works even when the shell
    opencode spawns has no node on PATH. The directory belongs to one benchmark run, preventing
    concurrent runs from retargeting each other's executable. Idempotent; returns the dir to prepend
    to PATH."""
    d = os.path.join(run_dir, "shell-env", "bin")
    os.makedirs(d, exist_ok=True)
    shim = os.path.join(d, "agent-device")
    body = f'#!/bin/sh\nexec {shlex.quote(node())} {shlex.quote(agent_device())} "$@"\n'
    if os.path.exists(shim):
        with open(shim) as f:
            existing = f.read()
        if existing != body:
            raise RuntimeError(
                f"benchmark run shim {shim} already pins a different executable"
            )
    else:
        with open(shim, "w") as f:
            f.write(body)
        os.chmod(shim, 0o755)
    return d


def agent_device_shell_env(run_dir, inherited_path):
    """Pin the run-owned agent-device shim after interactive shells source user startup files."""
    shim_dir = agent_device_shim_dir(run_dir)
    shell_dir = os.path.dirname(shim_dir)
    body = f'export PATH="{shim_dir}:$PATH"\n'
    startup = os.path.join(shell_dir, "agent-device-path.sh")
    for path in (os.path.join(shell_dir, ".zshenv"), startup):
        if os.path.exists(path):
            with open(path) as f:
                existing = f.read()
            if existing != body:
                raise RuntimeError(
                    f"benchmark run shell startup {path} already pins a different PATH"
                )
        else:
            with open(path, "w") as f:
                f.write(body)
    return {
        "PATH": shim_dir + os.pathsep + inherited_path,
        "ZDOTDIR": shell_dir,
        "BASH_ENV": startup,
        "ENV": startup,
    }


def sandbox_wrap(cmd, udid, denied_read_paths=()):
    """Wrap a shell-driven agent in a sandbox that cannot destroy existing simulators.

    Deny writes to the device set except the run's own clone, protect recovery artifacts and the
    ownership journal, and deny raw simulator command-line entry points. The trusted harness starts
    the clone-scoped agent-device daemon before entering this sandbox.

    CoreSimulatorService itself remains available. The trusted daemon can therefore keep using the
    service, while the model process cannot invoke xcrun/simctl directly or edit another simulator's
    files. BENCH_DEVICE_SET still supplies an optional separate blast boundary when configured.

    Reads stay allowed except for explicit `denied_read_paths`. Runners use that narrow exception
    for their historical result root, preventing an agent from recovering answers or credentials
    from prior transcripts. The harness opens the active transcript before spawning the sandboxed
    process, so stdout still flows through that inherited file descriptor.

    No-ops (returns cmd unchanged) if sandbox-exec is missing, so a non-macOS host still runs.
    """
    if not os.path.exists(SANDBOX_EXEC):
        return cmd
    # realpath, always: the sandbox matches CANONICAL paths, so a subpath naming a symlinked parent
    # (/var -> /private/var is the classic) silently matches nothing and the rule is a no-op.
    devices = os.path.realpath(device_set())
    own = os.path.join(devices, udid) if udid else None
    rules = ['(version 1)', '(allow default)',
             # Trusted device daemons are started by the harness. The model process must not bypass
             # them with raw simulator lifecycle commands against pre-existing devices.
             '(deny process-exec (literal "/usr/bin/xcrun"))',
             '(deny file-read-data (literal "/usr/bin/xcrun"))',
             '(deny file-write* (subpath %s))' % json.dumps(devices)]
    try:
        result = subprocess.run(
            ["/usr/bin/xcrun", "--find", "simctl"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        simctl = os.path.realpath(result.stdout.strip()) if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        simctl = ""
    if simctl:
        rules.extend([
            '(deny process-exec (literal %s))' % json.dumps(simctl),
            '(deny file-read-data (literal %s))' % json.dumps(simctl),
        ])
    if own:                                   # last match wins in SBPL: re-allow just this clone
        rules.append('(allow file-write* (subpath %s))' % json.dumps(own))
    for keep in ("~/golden-backups", "~/apps"):   # the only copies of the goldens and the app bundle
        p = os.path.expanduser(keep)
        if os.path.isdir(p):
            rules.append('(deny file-write* (subpath %s))' % json.dumps(os.path.realpath(p)))
    # The trusted harness journals clone ownership here. The sandboxed model must not be able to
    # authorize deletion by forging an entry.
    rules.append(
        '(deny file-write* (subpath %s))' % json.dumps(simulator_state_dir())
    )
    for path in denied_read_paths:
        rules.append('(deny file-read* (subpath %s))' % json.dumps(os.path.realpath(path)))
    return [SANDBOX_EXEC, "-p", "\n".join(rules)] + list(cmd)


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
    b = _which(argent()) or argent()
    d = os.path.dirname(os.path.realpath(b))
    while d and d != "/":
        pj = os.path.join(d, "package.json")
        if os.path.exists(pj):
            try:
                if "argent" in (json.load(open(pj)).get("name") or ""):
                    return d
            except Exception:
                pass
        d = os.path.dirname(d)
    return None


def argent_skills_dir():
    p = argent_pkg_dir()
    return os.path.join(p, "skills") if p else None


def argent_rule_file():
    p = argent_pkg_dir()
    return os.path.join(p, "rules", "argent.md") if p else None


def argent_agents_dir():
    p = argent_pkg_dir()
    return os.path.join(p, "agents") if p else None


AGENT_DEVICE_SKILL_NAMES = ("agent-device", "ios-simulator", "android-emulator")


def agent_device_skill_dirs():
    """Installed public agent-device skills, and only those skills.

    The npm package intentionally does not contain skills. A normal installation puts them in an
    agent skill root such as ~/.agents/skills. Selecting the known public names avoids leaking the
    user's unrelated ambient skills into benchmark runs.
    """
    env = os.environ.get("BENCH_AGENT_DEVICE_SKILLS")
    if env:
        candidates = [p for p in env.split(os.pathsep) if p]
    else:
        candidates = []
        repo_skills = os.path.join(agent_device_dir(), "skills")
        for root in (repo_skills, os.path.expanduser("~/.agents/skills"),
                     os.path.expanduser("~/.claude/skills")):
            for name in AGENT_DEVICE_SKILL_NAMES:
                candidates.append(os.path.join(root, name))
    found = []
    for path in candidates:
        path = os.path.realpath(os.path.expanduser(path))
        if os.path.isfile(os.path.join(path, "SKILL.md")) and path not in found:
            found.append(path)
    return found


def device_set():
    return os.environ.get("BENCH_DEVICE_SET") or DEFAULT_DEVICE_SET


def booted_udids():
    """UDIDs of currently-booted simulators (empty on failure)."""
    try:
        r = subprocess.run(
            ["xcrun", "simctl", "--set", device_set(), "list", "devices", "booted"],
            capture_output=True,
            text=True,
            timeout=20,
        )
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
        "device_set": device_set(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(resolve(), indent=2))
