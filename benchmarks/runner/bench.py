#!/usr/bin/env python3
"""silver-bench runner. Drives the N(model) x M(tool) matrix over the 30-task set through OpenCode,
capturing per-run transcript + final screenshot + metadata. Resumable, timeouts, health checks.

FULL PER-RUN ISOLATION: every run executes on a fresh CLONE of a golden simulator (sim_device.py),
behind a host-wide single-device lock, with all bench processes killed-and-verified at both ends of
the run and server-side app state reset by per-app hooks (isolation.py). Nothing survives a run:
not the device, not the MCP servers, not the proxies. See docs/surfaces/golden-simulator.md.

Usage:
  python3 bench.py --list                       # show cells/tasks
  python3 bench.py --cell silver:argent --task bsky-01   # single run
  python3 bench.py --cell silver:argent         # one whole cell (all tasks)
  python3 bench.py --all                         # full matrix (resumable; skips completed)
  python3 bench.py --all --apps bluesky,element  # restrict to some apps
  python3 bench.py --verify-golden               # one throwaway clone, launch each app, screenshot
"""
import os, sys, json, time, subprocess, argparse, shutil, tempfile, shlex
import ledger, bench_env, isolation, sim_device, gen_configs, bluesky_control

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)                    # the benchmark suite is one tree under the repo root
RESULTS = os.path.join(REPO, "data")            # run artifacts are the repo's published dataset
CONFIGS = os.path.join(ROOT, "configs")
# Machine-specific values resolve via bench_env: env override -> auto-detect -> documented fallback.
# This is what makes the bench portable — no per-machine constant editing. Old hardcoded values are kept
# only as last-resort fallbacks so an unusual host still has *something* rather than a bare crash.
# NOTE: there is deliberately NO device UDID here — the device is a per-run clone (sim_device.py).
OPENCODE = bench_env.opencode()
ADEV = bench_env.agent_device()
ADEV_DIR = bench_env.agent_device_dir()
NODE = bench_env.node()
ARGENT_BIN = bench_env.argent()


def pinned_tool_versions():
    """Pinned agent-tool versions the results should correspond to (configs/tool-versions.json)."""
    try:
        with open(os.path.join(CONFIGS, "tool-versions.json")) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except Exception:
        return {}


def detect_tool_version(tool):
    """Version of the tool actually installed on this machine (or None if undetectable)."""
    try:
        if tool == "argent":
            out = subprocess.run([ARGENT_BIN, "--version"], capture_output=True, text=True, timeout=10)
            return out.stdout.strip() or None
        if tool == "agent-device":
            with open(os.path.join(ADEV_DIR, "package.json")) as f:
                return json.load(f).get("version")
    except Exception:
        return None
    return None


def check_tool_versions():
    """Print pinned vs installed tool versions and WARN (non-fatal) on drift, so a run is always tied to
    the versions it claims (configs/tool-versions.json). Never aborts a run."""
    pinned = pinned_tool_versions()
    for tool in TOOLS:
        want, got = pinned.get(tool), detect_tool_version(tool)
        ok = bool(want) and got == want
        state = "ok" if ok else ("DRIFT" if want and got else "?")
        print(f"tool {tool}: pinned {want or '-'} · installed {got or '?'}  [{state}]", flush=True)
        if want and got and got != want:
            print(f"  !! WARNING: {tool} installed {got} != pinned {want} — bump configs/tool-versions.json "
                  f"if intentional, else results won't match the pin", flush=True)


SOCIAL_APP_DIR = os.path.expanduser("~/dev/social-app")     # Bluesky (Expo dev-client) source checkout
ELEMENT_IOS_DIR = os.path.expanduser("~/dev/element-ios")   # Element Classic (Riot) source checkout


def pinned_app_versions():
    """Frozen target-app versions the results correspond to (configs/app-versions.json)."""
    try:
        with open(os.path.join(CONFIGS, "app-versions.json")) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except Exception:
        return {}


def detect_app_version(app):
    """Version of the target app's local checkout — where each app declares it:
    Bluesky = package.json, Element = Config/AppVersion.xcconfig."""
    try:
        if app == "bluesky":
            with open(os.path.join(SOCIAL_APP_DIR, "package.json")) as f:
                return json.load(f).get("version")
        if app == "element":
            with open(os.path.join(ELEMENT_IOS_DIR, "Config", "AppVersion.xcconfig")) as f:
                for line in f:
                    if line.strip().startswith("MARKETING_VERSION"):
                        return line.split("=", 1)[1].strip()
    except Exception:
        return None
    return None


def check_app_versions():
    """Print/warn (non-fatal) the frozen target-app versions vs the local checkout. The commit is recorded
    in the manifest for provenance (the checkout has no .git, so only the version is verifiable here)."""
    for app, info in pinned_app_versions().items():
        want, got = info.get("version"), detect_app_version(app)
        commit = (info.get("commit") or "")[:7]
        ok = bool(want) and got == want
        state = "ok" if ok else ("DRIFT" if want and got else "?")
        print(f"app  {app}: pinned {want or '-'}{f' @{commit}' if commit else ''} · installed {got or '?'}  [{state}]",
              flush=True)
        if want and got and got != want:
            print(f"  !! WARNING: {app} checkout is {got} != pinned {want} — bump configs/app-versions.json "
                  f"if intentional, else results won't match the pin", flush=True)


# Bump when the run pipeline changes in a way that makes older results non-comparable (e.g. the move
# to full per-run isolation). Combined with the tool/app versions below it tells you, per result,
# WHICH runs are stale and should be overwritten (e.g. every argent 0.14.0 run once 0.15.0 ships).
SCHEMA_VERSION = 2       # 1 = pre-isolation legacy runs (no `versions` block at all); 2 = isolated harness
HARNESS = "isolation-v3-progressive"   # Published cohort stamp; report_data keeps this cohort intact.
RUN_HARNESS_BY_TOOL = {
    "argent": "isolation-v4-scoped-tools",
    "agent-device": "isolation-v4-scoped-tools",
    "none": "isolation-v4-scoped-tools",
}


def harness_for(tool):
    """Harness stamp for newly captured runs; HARNESS remains the published cohort stamp."""
    return RUN_HARNESS_BY_TOOL.get(tool, HARNESS)
                                  # v2 = skills loaded, but WRONG: the full skill text was always-injected
                                  # via `instructions`, AND the machine's ambient ~/.agents/~/.claude skills
                                  # (pptx, runpodctl, ...) leaked into every run. Both v1 & v2 are polluted.
                                  # v3 = faithful: each tool's REAL shipped skills staged in .opencode/skills
                                  # and PROGRESSIVELY disclosed (index + on-demand `skill` load, exactly as a
                                  # real agent has them); ambient skills suppressed; always-on rule only for
                                  # the tool's alwaysApply rule (argent). See gen_configs._stage_skills.

# How each tool is actually used in the wild — and thus how the benchmark drives it. Stamped into
# every run so a faithful (skills) run is never confused with a pre-skills one.
#   none = the BASELINE: no device-control or shell tool. This v4 control is intentionally distinct
#   from the published v3 shell baseline, and the harness stamp prevents cross-cohort comparison. The
#   preamble NEUTRALLY lists what is available (no imperative). Measures what a plain coding-agent
#   harness achieves with zero specialised affordance (expected low; that is the point of the control).
SURFACE = {"argent": "mcp", "agent-device": "cli", "none": "none"}

_versions_cache = {}


def run_versions(tool, app):
    """The provenance block stamped into every run's meta.json. Lets a later pass decide what to
    overwrite: e.g. tool != current, harness != current (pre-skills → repollute), or app drifted.
    Cached per (tool,app) since version detection shells out."""
    key = (tool, app)
    if key not in _versions_cache:
        _versions_cache[key] = {
            "schema": SCHEMA_VERSION,
            "harness": harness_for(tool),
            "surface": SURFACE.get(tool),               # "mcp" (argent) | "cli" (agent-device) | "none"
            "state_scope": "per-run" if tool == "agent-device" else None,
            "skills": tool != "none",                   # the tool's skill/rule bundle was loaded (none ships none)
            # This refresh is agent-device-scoped. Do not invalidate unrelated tool cohorts merely
            # because their provenance schema learned a new optional field.
            "skill_hashes": gen_configs.skill_manifest(tool) if tool == "agent-device" else None,
            "tool": detect_tool_version(tool),          # actual installed argent / agent-device version
            "tool_pinned": pinned_tool_versions().get(tool),
            "app": detect_app_version(app),             # target-app version (Bluesky checkout, etc.)
            "app_pinned": (pinned_app_versions().get(app) or {}).get("version"),
        }
    return _versions_cache[key]


def model_route(model_key):
    return "vercel-ai-gateway" if model_key.startswith("haiku") else "direct"

# FOCUS (2026-07-08): the ONLY SoTA cells we run/regenerate for now are gpt_low, gpt_high, haiku_low,
# haiku_high (gpt-5.4-mini + haiku-4.5, high/low effort). Do NOT run any other SoTA model. The rest stay
# in this map for history + so old results still resolve, but they are not queued. (Silver/gemma local
# models are separate — those run on the silver branch.) Report defaults every non-focus model hidden.
MODELS = {                                  # cell-model -> opencode provider/model id
    "silver": "ollama/silver-v8:e4b-text-Q6-K",  # silver-v8 (retrained; falls back set at package time)
    "gemma":  "ollama/gemma4-e4b-131k",      # untuned base, num_ctx 131072 to match silver (fair harness fit)
    "haiku":  "anthropic/claude-haiku-4-5",  # Anthropic Messages shape, routed through Vercel Gateway
    "gpt":    "openai/gpt-5.4-mini",
    "gpt55":  "openai/gpt-5.5",              # medium reasoning effort (EFFORT below, via the per-run proxy)
    "opus":   "anthropic/claude-opus-4-8",   # medium adaptive thinking (EFFORT below, via the per-run proxy)
    # reasoning-effort variants: same real model id; effort is injected by the PER-RUN proxies
    # (runner/*-proxy.js, effort fixed at spawn via BENCH_EFFORT_JSON from the EFFORT map below).
    "gpt_none": "openai/gpt-5.4-mini",       # no-thinking (reasoning_effort minimal)
    "gpt_low":  "openai/gpt-5.4-mini",       # low
    "gpt_high": "openai/gpt-5.4-mini",       # high
    "haiku_low":  "anthropic/claude-haiku-4-5",   # low thinking through Vercel AI Gateway
    "haiku_med":  "anthropic/claude-haiku-4-5",   # medium thinking
    "haiku_high": "anthropic/claude-haiku-4-5",   # high thinking
    "silverv9": "ollama/silver-v9rsn:e4b",  # reasoning-in-loss test         # standalone OpenAI API key in opencode auth.json
    "silverv9rsn2": "ollama/silver-v9rsn2:e4b",  # reorder-fix test (reasoning rendered BEFORE tool_call)
    "silverv9fmt": "ollama/silver-v9:e4b",  # format-fix (train==serve) — beats base ~1.7x on the grounding probe
    "silverv10": "ollama/silver-v10:e4b",  # v10 reasoning-ON @ ckpt-50
    "silverv10rsn3": "ollama/silver-v10rsn3:e4b",  # v10 data + completion-mask fix (full retrain)
    "silverv11rl": "ollama/silver-v11rl:e4b",  # v11 RFT (rejection FT)
}
# Reasoning-effort per model key, injected by the PER-RUN proxies via BENCH_EFFORT_JSON (isolation.py).
# Single source of truth — run_matrix.py's PLAN references these keys. {} = passthrough (no injection).
# This used to live in a shared mutable /tmp control file, which (a) raced parallel streams and
# (b) silently gave a standalone `bench.py --cell haiku_med:argent` whatever effort /tmp last held.
EFFORT = {
    "gpt_none":   {"gpt54": "none"},
    "gpt_low":    {"gpt54": "low"},
    "gpt":        {"gpt54": "medium"},
    "gpt_high":   {"gpt54": "high"},
    "gpt55":      {"gpt55": "medium"},
    "haiku":      {},                      # no thinking (proxy passthrough)
    "haiku_low":  {"haiku": "low"},
    "haiku_med":  {"haiku": "medium"},
    "haiku_high": {"haiku": "high"},
    "opus":       {"opus": "medium"},      # opus always thinks (proxy would default to medium anyway)
}
TOOLS = ["argent", "agent-device", "none"]   # each maps to configs/<tool>/opencode.json. `none` = the
                                             # baseline (no device MCP, no skills — just the stock shell)
PY = sys.executable or "python3"
# NATIVE apps (not browser). Each app -> {bundle: iOS bundle id, open: optional deep-link to force a
# known entry state}. reset_app terminates then (re)launches the NATIVE app on the run's fresh clone.
# Bluesky is an Expo dev-client: it loads the JS bundle from Metro (run `yarn start` in ~/dev/social-app
# first — see docs/surfaces/bluesky.md). Expensify/Immich are off until built+verified as native apps.
#
# Server-reset hooks (full isolation — the device clone resets CLIENT state only; these reset the
# server side): `reset_pre` runs BEFORE the clone is created (seed must land before the fresh app
# first syncs), `reset_post` runs after the agent (cleanup of a real remote account). "{t0}" in a
# hook cmd is replaced with the run's start unix-ts. Hooks are hard-fail: a failed reset means the
# next run would be polluted, so the matrix aborts loudly (BENCH_RESET_SOFT=1 to downgrade while
# debugging). Contract for every hook: idempotent, and NEVER invalidate the golden's stored login
# sessions (the element seed's logout_devices=False pattern).
# `load_wait` overrides LOAD_WAIT per app (element must re-sync its room list after a reseed).
APPS = {
    "bluesky":   {"bundle": "xyz.blueskyweb.app", "open": None,   # plain launch: release app w/ bundled JS
                  # SELF-HOSTED atproto (dev-env) surface: our own PDS(:3000)+AppView(:2584) seeded with a
                  # deterministic cat/dog world (tasks/setup/bluesky/bench-server.ts), the atproto analogue
                  # of the self-hosted Synapse used for element. The app logs into bench.test on the local
                  # PDS. Per-run reset = the bench-server control API: POST :1987/reset wipes ONLY bench's
                  # mutations (likes/reposts/replies/follows) + restores the seed follows, leaving the static
                  # content accounts + posts untouched (accounts/DIDs stay stable so the golden login lives).
                  "reset_pre":  ["curl", "-sf", "-m", "60", "-X", "POST", "http://localhost:1987/reset"],
                  "reset_post": ["curl", "-sf", "-m", "60", "-X", "POST", "http://localhost:1987/reset"],
                  "reset_timeout": 120},
    "element":   {"bundle": "im.vector.app", "open": None,   # native Element Classic (element-ios/Riot) ->
                  # local Synapse http://localhost:8008. Built from ~/dev/element-ios (default homeserver
                  # patched to localhost:8008). Logged in ONCE as alice (session frozen into the golden).
                  "reset_pre": [PY, os.path.join(ROOT, "tasks", "setup", "element", "seed.py")],
                  "reset_timeout": 300,
                  "load_wait": 25},   # reseed recreates rooms with new ids -> app must resync on launch
}
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "900"))   # per-run wall-clock cap (s)
LOAD_WAIT  = int(os.environ.get("LOAD_WAIT", "8"))        # seconds to let the native app settle after relaunch

def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def write_json(path, value):
    with open(path, "w") as stream:
        json.dump(value, stream, indent=2)


def load_tasks():
    with open(os.path.join(ROOT, "tasks", "tasks.json")) as stream:
        d = json.load(stream)
    return d["tasks"]

def reset_app(app, udid):
    """Bring the NATIVE app to a known entry state ON THE RUN'S CLONE: verify it's installed (i.e. was
    installed on the golden), terminate, then launch. Raises RuntimeError if the native app is missing or
    won't launch — NEVER silently fall back to the Safari/web surface (the v1 bug: an uninstalled bundle
    made simctl launch fail and left Safari up)."""
    spec = APPS[app]
    bundle, open_url = spec["bundle"], spec.get("open")
    simctl = f"xcrun simctl --set {shlex.quote(bench_env.device_set())}"
    if sh(f"{simctl} get_app_container {udid} {bundle}").returncode != 0:
        raise RuntimeError(f"native app '{bundle}' not installed on {udid} — re-golden?")
    sh(f"{simctl} terminate {udid} {bundle} 2>/dev/null")
    time.sleep(1)
    if open_url:
        r = sh(f'{simctl} openurl {udid} "{open_url}"')
    else:
        r = sh(f"{simctl} launch {udid} {bundle}")
    if r.returncode != 0:
        raise RuntimeError(f"failed to launch '{bundle}': {(r.stderr or '').strip()[:160]}")
    time.sleep(spec.get("load_wait", LOAD_WAIT))

def screenshot(udid, path):
    simctl = f"xcrun simctl --set {shlex.quote(bench_env.device_set())}"
    sh(f'{simctl} io {udid} screenshot "{path}"')

def reset_hook(app, phase, outdir, t0=None):
    """Run the app's server-reset hook for `phase` ('pre'/'post'), if any. See the APPS docstring for
    the contract; failures raise isolation.ResetError (hard-fail) unless BENCH_RESET_SOFT=1.
    Placeholders: {t0} = this run's start ts; {baseline} = the account's at-rest baseline ts from
    configs/golden.json (bsky_at_rest_ts) — required if the hook uses it."""
    cmd = APPS.get(app, {}).get(f"reset_{phase}")
    if not cmd:
        return None
    if any("{baseline}" in a for a in cmd):
        baseline = sim_device.load_manifest().get("bsky_at_rest_ts") or 0
        if not baseline:
            raise isolation.ResetError(
                f"{app} reset_{phase} needs bsky_at_rest_ts in configs/golden.json — set it to the "
                f"unix ts when the account was verified at-rest during golden creation "
                f"(docs/surfaces/golden-simulator.md)")
        cmd = [a.replace("{baseline}", str(int(baseline))) for a in cmd]
    cmd = [a.replace("{t0}", str(int(t0 or time.time()))) for a in cmd]
    return isolation.run_reset_hook(cmd, outdir, timeout=APPS[app].get("reset_timeout", 300),
                                    label=f"{app}-{phase}")

def preamble(app, udid, tool):
    """Task framing the agent sees. Deliberately thin: it names the environment and the tool's real
    interaction surface, then leaves the *how* to the tool's skill/rule bundle (loaded as opencode
    `instructions`) — exactly what a real agent would have. argent is MCP-first; agent-device is a
    CLI the agent drives through a shell."""
    is_web = APPS.get(app, {}).get("bundle") == "com.apple.mobilesafari"
    bundle = APPS.get(app, {}).get("bundle", app)
    surface = (f"The {app} web app is already open in Mobile Safari" if is_web
               else f"The native {app} iOS app (bundle id {bundle}) is already open and in the foreground")
    if tool == "agent-device":
        how = (f"Drive it with the agent-device CLI, which you run as shell commands. This simulator is "
               f"already agent-device's default target, so you do not need to pass --udid. Consult your "
               f"available agent-device skill(s) and the CLI's own help for the workflow.")
    elif tool == "none":
        how = "No device-control or shell tool is provided in this control condition."
    else:
        how = (f"Drive it with the argent MCP tools. Consult your available argent skills for how to "
               f"interact with the device.")
    lead = f"You are an autonomous agent operating an iOS Simulator (device udid {udid}). {surface}."
    tail = ("Do NOT ask the user any questions or request clarification — act autonomously and make "
            "reasonable choices yourself. Inspect the screen, then act, then re-inspect, repeating until "
            "the task is done. When done, stop.")
    # `how` is empty for the `none` baseline (no tool hint at all) — the join drops it, no stray gap.
    return " ".join(p for p in (lead, how, tail) if p)

def parse_transcript(path):
    n_tools, names = 0, []
    try:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "tool_use":
                n_tools += 1
                t = (ev.get("part") or {}).get("tool")
                if t:
                    names.append(t)
    except FileNotFoundError:
        pass
    return n_tools, names


def sandbox_agent_command(command, tool, udid):
    if tool != "agent-device":
        return command
    return bench_env.sandbox_wrap(command, udid=udid, denied_read_paths=[RESULTS])


def start_clone_scoped_agent_device_daemon(config_path, state_dir):
    """Start the daemon outside the model sandbox, scoped to this run's disposable clone.

    `devices` only boots the daemon and reads device inventory. It does not start the XCTest runner,
    inspect the app, or expose task state to the model.
    """
    env = {
        **os.environ,
        "AGENT_DEVICE_CONFIG": config_path,
        "AGENT_DEVICE_STATE_DIR": state_dir,
    }
    result = subprocess.run(
        [ADEV, "devices", "--platform", "ios", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not start clone-scoped agent-device daemon: {(result.stderr or '')[-300:]}"
        )


def agent_command(model_key, prompt):
    """Build an unattended OpenCode command that still honors explicit permission denials."""
    return [
        OPENCODE,
        "run",
        "--format",
        "json",
        "--model",
        MODELS[model_key],
        "--auto",
        prompt,
    ]


def reset_agent_services_for_retry(tool, udid, state_dir, config_path):
    """Tear down one failed attempt and restore trusted services needed by the next attempt."""
    isolation.teardown_all(
        "retry",
        adev=(ADEV if tool == "agent-device" else None),
        adev_udid=udid,
        adev_state_dir=(state_dir if tool == "agent-device" else None),
    )
    if tool == "agent-device":
        start_clone_scoped_agent_device_daemon(config_path, state_dir)


def _surface_meta(model_key, tool, task, err, t0):
    """meta.json for a run that never reached the agent (rc=-2 SURFACE_ERROR): the ledger keeps the
    unit pending, so it self-heals on the next pass."""
    return {"cell": f"{model_key}:{tool}", "model": model_key, "tool": tool, "task": task["id"],
            "app": task["app"], "kind": task["kind"],
            "nav_category": task.get("nav_category", task["kind"]), "needs_auth": task["needs_auth"],
            "returncode": -2, "timed_out": False, "wall_s": 0.0, "n_tool_calls": 0, "tool_names": [],
            "stderr_tail": f"SURFACE_ERROR: {err}", "ts": int(t0),
            "versions": {**run_versions(tool, task["app"]), "model_route": model_route(model_key)}}


def run_one(model_key, tool, task, force=False):
    """One fully isolated run. Lifecycle (each phase leaves an audit trail in the run's env.json):
      teardown(pre) -> observe concurrent simulators -> server reset_pre -> clone golden -> boot -> per-run
      config -> launch app -> per-run proxies -> opencode (own process group) -> screenshot/meta
      finally: server reset_post -> teardown(post, verified) -> retire clone -> env.json
    Failure semantics: clone/boot/app-launch failures record SURFACE_ERROR rc=-2 (unit stays pending,
    matrix continues). TeardownError / ResetError / a bad golden PROPAGATE —
    the machine can no longer guarantee isolation, so the matrix must stop loudly."""
    cell = f"{model_key}:{tool}"
    outdir = os.path.join(RESULTS, cell.replace(":", "__"), task["id"])
    transcript = os.path.join(outdir, "transcript.jsonl")
    meta_path = os.path.join(outdir, "meta.json")
    # idempotent, but harness-aware: skip a unit only if it already has a valid result AT THE CURRENT
    # harness. A never-run / surface-errored unit is still 'pending', and a completed/judged unit from
    # an OLDER (polluted) harness needs_run=True so this pass REGENERATES over it — otherwise a judged
    # v1/v2 result would be skipped forever and never faithfully re-run.
    expected_versions = {**run_versions(tool, task["app"]), "model_route": model_route(model_key)}
    stale = ledger.needs_run(
        RESULTS, model_key, tool, task["id"], harness_for(tool), expected_versions
    )
    if (not force) and not stale:
        print(f"  SKIP {cell}/{task['id']} ({ledger.state_of(RESULTS, model_key, tool, task['id'])} @ current harness)")
        with open(meta_path) as stream:
            return json.load(stream)
    # Replacement is deliberate (new tool/app/skill provenance or an explicit retry). The ledger's
    # monotonic state is correct for normal progress, but must not let the previous judged state mask
    # this fresh, not-yet-judged capture.
    ledger.reset(RESULTS, model_key, tool, task["id"])
    # remote-endpoint resilience: if this is an ollama model the active endpoint isn't serving right
    # now (e.g. the Kaggle tunnel died mid-pass), DEFER — leave the unit pending rather than run it
    # against a dead endpoint and record a fake failure. Writes nothing, so the next pass retries.
    if not model_runnable(model_key, ollama_served_cached()):
        print(f"  DEFER {cell}/{task['id']} (endpoint not serving {MODELS.get(model_key)})")
        return None
    os.makedirs(outdir, exist_ok=True)
    # a stale score.json from an earlier judged-then-purged/surface-errored/forced attempt would
    # otherwise auto-promote THIS fresh run straight to 'judged' with the old verdict (disk_state
    # checks score.json by name) — clear it so the new screenshot gets genuinely judged.
    try:
        os.remove(os.path.join(outdir, "score.json"))
    except FileNotFoundError:
        pass
    effort = EFFORT.get(model_key, {})
    t0 = time.time()
    env = {"ts_start": int(t0), "effort": effort, "golden": None, "clone_udid": None,
           "concurrent_booted_simulators": [],
           "reaped_owned_clones": [],
           "bluesky_setup": None,
           "postcondition": None,
           "proxies": None, "reset_pre": None, "reset_post": None,
           "teardown_pre": None, "teardown_post": None}
    udid = cfg_dir = None
    meta = None
    agent_ran = False
    bluesky_identity = None
    golden_refreshed = False
    try:
        # ---- clean slate: terminate processes proven to belong to this wrapper invocation, retire
        # clones proven by the durable ownership journal to belong to an interrupted benchmark, and
        # observe all other simulators without mutating them.
        env["teardown_pre"] = isolation.teardown_all("pre")
        env["reaped_owned_clones"] = sim_device.reap_owned_clones()
        env["concurrent_booted_simulators"] = sim_device.check_device_conflicts()
        env["golden"] = {k: sim_device.check_golden()[k] for k in ("name", "udid")}
        if task["app"] == "bluesky":
            try:
                bluesky_identity = bluesky_control.backend_identity()
            except bluesky_control.BlueskyControlError as e:
                raise sim_device.DeviceError(str(e)) from e
            sim_device.require_bluesky_backend_identity(bluesky_identity)
        # ---- server-side state reset BEFORE the fresh clone's app first syncs (and before the
        # session-refresh boot, so a misconfigured hook fails before any device work)
        env["reset_pre"] = reset_hook(task["app"], "pre", outdir, t0)
        if task["app"] == "bluesky":
            golden_refreshed = sim_device.refresh_golden_sessions()
        # ---- fresh byte-identical device for this run
        try:
            udid = sim_device.clone_golden(task["id"])
            env["clone_udid"] = udid
            sim_device.boot(udid)
        except sim_device.DeviceError as e:
            meta = _surface_meta(model_key, tool, task, f"device: {e}", t0)
            write_json(meta_path, meta)
            print(f"  FAIL {cell}/{task['id']}  device: {e}")
            return meta
        # ---- per-run opencode config: the tool's REAL shipped skills staged in .opencode/skills/ for
        # progressive disclosure; argent's always-on rule as instructions; argent MCP pointed at THIS
        # clone, or — for the CLI-first agent-device — no MCP + a CLI target config.
        cfg_dir = gen_configs.write_run_config(tool, udid)
        agent_device_state_dir = os.path.join(cfg_dir, "agent-device-state")
        # Ambient-skill suppression: expose ONLY this tool's staged skills, never the machine's globally
        # installed ~/.agents / ~/.claude skills (pptx, runpodctl, personal skills...). Both flags zero
        # those out; the native staged .opencode/skills set survives. Applied to EVERY run.
        run_env = {"OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
                   "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
                   # Current OpenCode does not reliably discover opencode.json from cwd when
                   # invoked non-interactively. Without this, Haiku bypasses the local effort
                   # proxy and calls Anthropic directly.
                   "OPENCODE_CONFIG": os.path.join(cfg_dir, "opencode.json")}
        proxy_env = {}
        if model_route(model_key) == "vercel-ai-gateway":
            gateway_key = bench_env.vercel_ai_gateway_key()
            if not gateway_key:
                raise RuntimeError(
                    "Vercel AI Gateway key missing (AI_GATEWAY_API_KEY or OpenCode provider 'vercel')")
            run_env["ANTHROPIC_API_KEY"] = gateway_key
            run_env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:8788"
            proxy_env["BENCH_ANTHROPIC_UPSTREAM"] = "vercel"
            proxy_env["AI_GATEWAY_API_KEY"] = gateway_key
        # agent-device is driven through a shell: put `agent-device` on PATH and pin this clone as the
        # CLI default target, so the agent's bare `agent-device ...` commands hit the run's device.
        if tool == "agent-device":
            run_env["AGENT_DEVICE_CONFIG"] = os.path.join(cfg_dir, "agent-device-cli.json")
            run_env["AGENT_DEVICE_STATE_DIR"] = agent_device_state_dir
            run_env.update(
                bench_env.agent_device_shell_env(cfg_dir, os.environ.get("PATH", ""))
            )
        try:
            reset_app(task["app"], udid)
        except RuntimeError as e:
            # record an explicit surface failure rather than running the agent against Safari
            screenshot(udid, os.path.join(outdir, "final.png"))
            meta = _surface_meta(model_key, tool, task, str(e), t0)
            write_json(meta_path, meta)
            print(f"  FAIL {cell}/{task['id']}  surface: {e}")
            return meta
        if task["app"] == "bluesky":
            try:
                clone_session = bluesky_control.validate_clone_session(udid, agent_device_state_dir)
            except bluesky_control.BlueskyControlError as e:
                screenshot(udid, os.path.join(outdir, "final.png"))
                meta = _surface_meta(model_key, tool, task, f"bluesky setup: {e}", t0)
                write_json(meta_path, meta)
                print(f"  FAIL {cell}/{task['id']}  bluesky setup: {e}")
                return meta
            sim_device.confirm_bluesky_clone_session(
                bluesky_identity,
                clone_authenticated=clone_session["authenticated"],
                refreshed=golden_refreshed,
            )
            env["bluesky_setup"] = {
                "backend_identity": bluesky_identity,
                "clone_session": clone_session,
                "golden_refreshed": golden_refreshed,
            }
        if tool == "agent-device":
            start_clone_scoped_agent_device_daemon(
                os.path.join(cfg_dir, "agent-device-cli.json"), agent_device_state_dir
            )
        # ---- fresh effort-injection proxies, fixed effort, logs into the run dir
        node = NODE if NODE and os.path.exists(NODE) else "node"
        env["proxies"] = isolation.start_proxies(effort, outdir, node=node,
                                                 providers=isolation.providers_needed(MODELS[model_key]),
                                                 env_overrides=proxy_env)
        # ---- the agent. Bounded retry on "0 tool calls" for tool-bearing cohorts: the MCP server
        # (argent) or CLI surface (agent-device)
        # occasionally hasn't registered its tools by the time the model takes its first turn, so
        # the model reports "no device-control tools available" and stops immediately (rc=0, ~8s,
        # 0 tools). That is a tool-unavailable flake, NOT a model result — a fresh opencode run
        # (which spawns a fresh MCP server) almost always gets the tools. Retry only that signature.
        full_prompt = preamble(task["app"], udid, tool) + "\n\nTASK: " + task["prompt"]
        agent_ran = True
        t_run = time.time()
        agent_cmd = agent_command(model_key, full_prompt)
        if tool == "agent-device":
            # The CLI-first cell has a real shell, and a PATH shim only shadows a command NAME —
            # `rm`, `mv`, `python`, or direct `simctl` could reach every simulator owned by this user.
            # A 0.20.8 refresh lost the complete device set while an agent-device cell was active.
            # Deny writes outside this run's clone at the kernel boundary. Normal CoreSimulator
            # actions still work because CoreSimulatorService performs them outside this sandbox.
            agent_cmd = sandbox_agent_command(agent_cmd, tool, udid)
        # Recorded per run: nothing outside can observe a seatbelt sandbox after the fact
        # (sandbox-exec execs in place, so there is no process to find, and sandbox_check(2) needs
        # entitlements). The spawn site is the only honest witness that the wrapper was applied.
        sandboxed = agent_cmd[0] == bench_env.SANDBOX_EXEC
        for attempt in range(3):
            with open(transcript, "w") as tf:
                rc, err, timed_out = isolation.run_opencode(
                    agent_cmd,
                    cwd=cfg_dir, timeout=RUN_TIMEOUT, stdout_file=tf, env=run_env)
            n_tools, names = parse_transcript(transcript)
            if tool == "none" or n_tools > 0 or timed_out or attempt == 2:
                break
            print(f"  retry {cell}/{task['id']}: 0 tool calls (MCP not ready?) attempt {attempt+1}", flush=True)
            reset_agent_services_for_retry(
                tool,
                udid,
                agent_device_state_dir,
                os.path.join(cfg_dir, "agent-device-cli.json"),
            )
            env["proxies"] = isolation.start_proxies(effort, outdir, node=node,   # teardown killed them
                                                     providers=isolation.providers_needed(MODELS[model_key]),
                                                     env_overrides=proxy_env)
        wall = round(time.time() - t_run, 1)
        screenshot(udid, os.path.join(outdir, "final.png"))
        postcondition = (
            bluesky_control.assert_mutation_postcondition(task["id"])
            if task["app"] == "bluesky"
            else None
        )
        env["postcondition"] = postcondition
        meta = {"cell": cell, "model": model_key, "tool": tool, "task": task["id"], "app": task["app"],
                "kind": task["kind"], "nav_category": task.get("nav_category", task["kind"]),
                "needs_auth": task["needs_auth"], "returncode": rc, "timed_out": timed_out,
                "wall_s": wall, "n_tool_calls": n_tools, "tool_names": names, "stderr_tail": err,
                "ts": int(t_run), "golden": env["golden"], "clone_udid": udid,
                "sandboxed": sandboxed,
                "postcondition": postcondition,
                "versions": {**run_versions(tool, task["app"]), "model_route": model_route(model_key)}}
        write_json(meta_path, meta)
        ledger.mark(RESULTS, model_key, tool, task["id"])     # advance pending -> completed (monotonic)
        print(f"  RAN  {cell}/{task['id']}  {wall}s  tools={n_tools} rc={rc}{' TIMEOUT' if timed_out else ''}")
        return meta
    finally:
        # ---- teardown, ALWAYS — every step guarded so an exception in one (even KeyboardInterrupt
        # during the up-to-300s post hook) can never skip the rest. Order: undo the run's server-side
        # mutations first (device-independent, and a TeardownError must not skip it), then kill+verify
        # every process owned by this invocation, then retire the clone according to policy, then
        # write the audit manifest. The most severe
        # failure propagates AFTER cleanup completes.
        post_err = None
        if agent_ran:
            try:
                env["reset_post"] = reset_hook(task["app"], "post", outdir, t0)
            except BaseException as e:          # ResetError, KeyboardInterrupt, OSError, ...
                post_err = e
        try:
            env["teardown_post"] = isolation.teardown_all(
                "post", adev=(ADEV if tool == "agent-device" else None), adev_udid=udid,
                adev_state_dir=(os.path.join(cfg_dir, "agent-device-state")
                                if tool == "agent-device" and cfg_dir else None))
        except BaseException as e:               # TeardownError, KeyboardInterrupt mid-teardown, ...
            post_err = post_err or e
        if udid and not sim_device.destroy(udid):
            # A clone that cannot be shut down and deleted makes the next run unsafe.
            post_err = post_err or isolation.TeardownError(
                f"clone {udid} could not be retired — CoreSimulator may be wedged")
        if cfg_dir:
            shutil.rmtree(cfg_dir, ignore_errors=True)
        env["ts_end"] = int(time.time())
        isolation.write_env_manifest(outdir, **env)
        if post_err:
            raise post_err

def ollama_served_tags():
    """Model tags the configured ollama endpoint currently serves (empty set on any failure).
    Reads the SAME baseURL opencode uses (local ollama, or a Kaggle tunnel) so the gate reflects
    what would actually answer a run. /v1 -> /api/tags."""
    base = "http://localhost:11434"
    # prefer the per-stream config's ollama baseURL (CONFIGS/argent, e.g. stream B's kernel-2 tunnel),
    # else the global config — so the gate probes the SAME endpoint this stream's opencode will use.
    for cfg_path in (os.path.join(CONFIGS, "argent", "opencode.json"),
                     os.path.expanduser("~/.config/opencode/opencode.json")):
        try:
            with open(cfg_path) as stream:
                config = json.load(stream)
            url = config.get("provider", {}).get("ollama", {}).get("options", {}).get("baseURL")
        except Exception:
            url = None
        if url:
            base = url.rstrip("/")[:-3] if url.rstrip("/").endswith("/v1") else url.rstrip("/")
            break
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            return {m["name"] for m in json.load(r).get("models", [])}
    except Exception:
        return set()

_served_cache = {"t": 0.0, "tags": set()}
def ollama_served_cached(ttl=45):
    """Cached ollama_served_tags() — one /api/tags hit per ~ttl seconds, so a per-unit availability
    check (run_one) stays cheap while still catching a mid-run endpoint death (dead Kaggle tunnel)."""
    now = time.time()
    if now - _served_cache["t"] > ttl:
        _served_cache["tags"] = ollama_served_tags()
        _served_cache["t"] = now
    return _served_cache["tags"]

def model_runnable(model_key, served):
    """Cloud models are always runnable; an ollama model only when its tag is actually served.
    Lets us SCHEDULE a model (e.g. silverv9fmt) whose artifact isn't on the active endpoint yet:
    its units stay 'pending' instead of being run-and-marked-failed (which would fake a 0% score)."""
    mid = MODELS.get(model_key, "")
    if not mid.startswith("ollama/"):
        return True
    tag = mid.split("/", 1)[1]
    # a tag without an explicit ":" implies ":latest" in ollama (e.g. gemma4-e4b-131k -> :latest)
    return tag in served or (":" not in tag and f"{tag}:latest" in served)


def incomplete_units(cells, tasks):
    """Units whose recorded provenance does not satisfy the current benchmark contract."""
    missing = []
    for model, tool in cells:
        for task in tasks:
            versions = {**run_versions(tool, task["app"]), "model_route": model_route(model)}
            if ledger.needs_run(RESULTS, model, tool, task["id"], harness_for(tool), versions):
                missing.append(f"{model}:{tool}/{task['id']}")
    return missing

def verify_golden():
    """One fresh clone: boot, launch every enabled app, screenshot each to /tmp, retire it. Run
    before big passes to eyeball that the golden's sessions are still logged in."""
    isolation.teardown_all("pre", adev=ADEV)
    sim_device.check_device_conflicts()
    g = sim_device.check_golden()
    print(f"golden: {g['name']} ({g['udid']}) Shutdown ok")
    sim_device.reap_owned_clones()
    udid = sim_device.clone_golden("verify")
    try:
        sim_device.boot(udid)
        for app in APPS:
            try:
                reset_app(app, udid)
                shot = f"/tmp/bench-golden-verify-{app}.png"
                screenshot(udid, shot)
                print(f"  {app}: launched, screenshot {shot}")
            except RuntimeError as e:
                print(f"  {app}: FAIL {e}")
    finally:
        if not sim_device.destroy(udid):
            raise sim_device.DeviceError(f"verification clone {udid} could not be retired")
    print("verify-golden done — eyeball the screenshots for logged-in state")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--cell")            # e.g. silver:argent
    ap.add_argument("--task")            # e.g. bsky-01
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--models")          # comma list to restrict (e.g. gemma,silver)
    ap.add_argument("--tools")           # comma list to restrict tools (e.g. argent) — else both
    ap.add_argument("--apps")            # comma list to restrict
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--require-complete", action="store_true",
                    help="fail after the pass if any selected unit is missing or stale")
    ap.add_argument("--wait-lock", action="store_true",
                    help="queue behind another bench stream instead of failing (still one at a time)")
    ap.add_argument("--verify-golden", action="store_true",
                    help="clone the golden once, launch each app, screenshot, then retire the clone")
    a = ap.parse_args()
    tasks = load_tasks()
    # only run tasks for apps actually ENABLED in APPS — so --all auto-covers an app the moment it's
    # added to APPS, and never KeyErrors on one whose native build does not exist yet. Annulled tasks
    # are excluded here too (run_matrix/doctor already do).
    tasks = [t for t in tasks if t["app"] in APPS and not t.get("annulled")]
    if a.apps:
        keep = set(a.apps.split(",")); tasks = [t for t in tasks if t["app"] in keep]

    if a.list:
        print("MODELS:", list(MODELS)); print("TOOLS:", TOOLS)
        print(f"TASKS: {len(tasks)} ", [t["id"] for t in tasks]); return

    # One benchmark stream at a time; unrelated explicitly-targeted simulators may coexist.
    isolation.acquire_lock(wait=a.wait_lock, label=f"bench {a.cell or ('all' if a.all else 'verify')}")

    if a.verify_golden:
        verify_golden(); return

    check_tool_versions()   # tie the run to the pinned argent / agent-device versions (warn on drift)
    check_app_versions()    # ... and to the frozen Bluesky (target app) version (warn on drift)
    sim_device.check_golden()   # fail fast before burning any API spend (session refresh is per-run)

    cells = []
    if a.cell:
        mk, tool = a.cell.split(":"); cells = [(mk, tool)]
    elif a.all:
        mks = a.models.split(",") if a.models else list(MODELS)
        tls = a.tools.split(",") if a.tools else TOOLS
        cells = [(mk, tool) for mk in mks for tool in tls]
    else:
        print("specify --cell, --all, or --list"); return

    # availability gate: don't run an ollama model whose tag the active endpoint isn't serving yet —
    # leave those units pending (a scheduled model gets measured automatically once it's served).
    served = ollama_served_tags()
    runnable = [(mk, tool) for mk, tool in cells if model_runnable(mk, served)]
    deferred = sorted({mk for mk, _ in cells} - {mk for mk, _ in runnable})
    if deferred:
        print(f"== DEFER (ollama tag not served; left pending): {deferred} ==")
    cells = runnable

    sel = [t for t in tasks if (not a.task or t["id"] == a.task)]
    print(f"== running {len(cells)} cell(s) x {len(sel)} task(s) ==")
    for mk, tool in cells:
        print(f"# CELL {mk}:{tool}")
        for t in sel:
            try:
                run_one(mk, tool, t, force=a.force)
            except (isolation.TeardownError, isolation.ResetError, sim_device.DeviceError):
                raise   # isolation can't be guaranteed — stop loudly (same policy as run_matrix)
            except Exception as e:
                # transient per-unit failure (e.g. ProxyError): unit stays pending, pass continues
                print(f"  {mk}:{tool}/{t['id']} ERROR {e}", flush=True)

    if a.require_complete:
        missing = incomplete_units(cells, sel)
        if missing:
            raise RuntimeError(f"benchmark incomplete ({len(missing)}): {', '.join(missing[:10])}")
        print("== selected benchmark pass complete ==", flush=True)

if __name__ == "__main__":
    main()
