#!/usr/bin/env python3
"""Local unit tests for how the harness runs opencode 2: the version guard and pin, the `opencode run`
command line, the run's stamps, and counting the MCP calls a code-mode script makes - no devices, no
network, no API spend.

    python3 runner/test_opencode.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench
import isolation
import ledger
import sim_device
from test_preload import TASK, fresh, harness, patched

PASS = 0
NO_PRELOAD = {"none": {"why": "t", "steps": [], "check": None}, "argent": {"why": "t", "steps": [], "check": None}}


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


def tool_use(tool, calls=None, nested=True):
    """A tool_use event as `opencode run --format json` prints it. opencode 2 records an execute's calls
    at state.metadata.metadata.toolCalls (`nested`); 1.x code mode had them one level up."""
    part = {"tool": tool}
    if calls is not None:
        record = {"toolCalls": [{"tool": c, "status": "completed"} for c in calls], "truncated": False}
        part["state"] = {"status": "completed", "metadata": {"metadata": record} if nested else record}
    return json.dumps({"type": "tool_use", "part": part}) + "\n"


def parsed(*lines):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write("".join(lines))
    try:
        return bench.parse_transcript(f.name)
    finally:
        os.unlink(f.name)


def test_parse_transcript_counts_code_mode_calls():
    check("a direct tool counts once", parsed(tool_use("skill"), tool_use("shell")) == (2, ["skill", "shell"]))
    got = parsed(tool_use("skill"), tool_use("execute", ["argent.describe", "argent.gesture-tap"]))
    check("an execute counts as the MCP calls its script made",
          got == (3, ["skill", "argent.describe", "argent.gesture-tap"]), got)
    got = parsed(tool_use("execute", ["search", "argent.describe"]))
    check("a catalog search inside the script is not a call", got == (1, ["argent.describe"]), got)
    got = parsed(tool_use("execute", ["argent.describe"], nested=False))
    check("the 1.x call list is read too", got == (1, ["argent.describe"]), got)
    got = parsed(tool_use("execute", []), tool_use("execute"), tool_use("execute", ["search"]))
    check("an execute that reached no tool counts once, as itself", got == (3, ["execute"] * 3), got)
    got = parsed("not json\n", json.dumps({"type": "text", "part": {}}) + "\n")
    check("other events and junk lines count nothing", got == (0, []), got)
    check("a missing transcript counts nothing", bench.parse_transcript("/nonexistent/t.jsonl") == (0, []))


def test_direct_mcp_calls():
    names = ["skill", "argent_describe", "argent.describe", "argent_gesture-tap"]
    check("direct MCP calls are counted for the MCP tool", bench.direct_mcp_calls("argent", names) == 2)
    check("code-mode calls are not", bench.direct_mcp_calls("argent", ["skill", "argent.describe"]) == 0)
    check("a CLI or shell tool has none", bench.direct_mcp_calls("agent-device", names) ==
          bench.direct_mcp_calls("none", names) == 0)


def test_require_opencode():
    for version, ok in (("1.4.3", False), ("1.19.6", False), ("2.0.0", True), ("2.3.0", True), (None, True)):
        with patched(bench, detect_opencode_version=lambda v=version: v):
            try:
                bench.require_opencode()
                check(f"opencode {version} is {'accepted' if ok else 'refused'}", ok)
            except bench.OpencodeVersionError as e:
                check(f"opencode {version} is {'accepted' if ok else 'refused'}", not ok and version in str(e), e)


def test_pin():
    pin = bench.pinned_opencode_version()
    check("an opencode version is pinned", bool(pin), pin)
    check("... and it is one the harness accepts", tuple(int(x) for x in pin.split(".")) >= bench.OPENCODE_SINCE, pin)


def test_run_one_command_and_stamps():
    with tempfile.TemporaryDirectory() as results, fresh(NO_PRELOAD), harness(results):
        seen = []

        def fake_opencode(cmd, cwd, timeout, stdout_file, env=None):
            seen.append(cmd)
            stdout_file.write(tool_use("skill") + tool_use("execute", ["search", "argent.describe", "argent.gesture-tap"]))
            return 0, "", False

        with patched(isolation, run_opencode=fake_opencode), patched(bench, detect_tool_version=lambda t: "9.9.9"):
            meta = bench.run_one("gpt_low", "argent", TASK)
        cmd = seen[0]
        check("opencode runs headless on a private server, auto-approving",
              cmd[:6] == [bench.OPENCODE, "run", "--standalone", "--auto", "--format", "json"], cmd[:6])
        check("... with no 1.x-only flag", "--dangerously-skip-permissions" not in cmd, cmd)
        check("... on the cell's model, the prompt last",
              cmd[6:8] == ["--model", bench.MODELS["gpt_low"]] and "TASK: " in cmd[-1] and len(cmd) == 9, cmd[6:8])
        v = meta["versions"]
        check("meta.json stamps the opencode version and its pin",
              (v.get("opencode"), v.get("opencode_pinned")) == ("2.3.0", bench.pinned_opencode_version()), v)
        check("... and how tools were loaded", v.get("tool_loading") == "code-mode", v)
        check("... under the current harness", v.get("harness") == bench.HARNESS, v)
        check("the run counts the device calls inside execute",
              (meta["n_tool_calls"], meta["tool_names"]) == (3, ["skill", "argent.describe", "argent.gesture-tap"]),
              meta)
        check("no direct MCP call is recorded", meta["mcp_direct_calls"] == 0, meta)


def test_run_one_flags_direct_mcp_calls():
    with tempfile.TemporaryDirectory() as results, fresh(NO_PRELOAD), harness(results):
        def fake_opencode(cmd, cwd, timeout, stdout_file, env=None):
            stdout_file.write(tool_use("argent_describe") + tool_use("argent_gesture-tap"))
            return 0, "", False

        with patched(isolation, run_opencode=fake_opencode), patched(bench, detect_tool_version=lambda t: "9.9.9"):
            meta = bench.run_one("gpt_low", "argent", TASK)
        check("a run whose MCP tools were called directly says so", meta["mcp_direct_calls"] == 2, meta)


def test_mobile_mcp_is_wired_as_an_mcp_tool():
    import gen_configs
    check("mobile-mcp is a benchmarked tool on the MCP surface, ahead of the baseline",
          bench.TOOLS.index("mobile-mcp") < bench.TOOLS.index("none") and bench.SURFACE["mobile-mcp"] == "mcp")
    check("its direct calls are told from its code-mode calls",
          bench.direct_mcp_calls("mobile-mcp", ["mobile-mcp_mobile_take_screenshot", "mobile-mcp.mobile_take_screenshot"]) == 1)
    text = bench.preamble("bluesky", "UDID-1", "mobile-mcp")
    check("the preamble names its tools and says the udid is the device id",
          "mobile-mcp MCP tools" in text and "UDID-1" in text and "udid as their device" in text
          and "skill" not in text, text)
    with patched(gen_configs.bench_env, mobile_mcp=lambda: "/opt/mcp-server-mobile"):
        d = gen_configs.write_run_config("mobile-mcp", "UDID-1")
    try:
        cfg = json.load(open(os.path.join(d, "opencode.json")))
        server = cfg["mcp"]["mobile-mcp"]
        check("the run config starts its stdio server, telemetry off",
              server["command"] == ["/opt/mcp-server-mobile"] and server.get("enabled") is True
              and server["environment"] == {"MOBILEMCP_DISABLE_TELEMETRY": "1"}, server)
        check("... and never opts it out of code mode", "codemode" not in server, server)
        check("it ships no skills or rule, so none are staged",
              cfg["instructions"] == [] and not os.path.exists(os.path.join(d, ".opencode")), cfg["instructions"])
    finally:
        __import__("shutil").rmtree(d, ignore_errors=True)


def test_run_one_gives_mobile_mcp_its_own_mobilecli_home():
    with tempfile.TemporaryDirectory() as results, harness(results), \
            fresh({"mobile-mcp": {"why": "t", "steps": [], "check": None}}):
        seen = []

        def fake_opencode(cmd, cwd, timeout, stdout_file, env=None):
            home = env.get("MOBILECLI_HOME")
            seen.append((home, os.path.isdir(home or "")))
            stdout_file.write(tool_use("execute", ["mobile-mcp.mobile_list_elements_on_screen"]))
            return 0, "", False

        with patched(isolation, run_opencode=fake_opencode), patched(bench, detect_tool_version=lambda t: "1.0.17"):
            meta = bench.run_one("gpt_low", "mobile-mcp", TASK)
        home, existed = seen[0]
        check("the run has a mobilecli home of its own, present while it runs", bool(home) and existed, seen)
        check("... short enough for mobilecli's socket path", len(os.path.join(home, "daemon.sock")) < 60, home)
        check("... and gone afterwards", not os.path.exists(home), home)
        check("the run records no skills and the MCP surface",
              meta["versions"]["skills"] is False and meta["versions"]["surface"] == "mcp", meta["versions"])
        check("... and counts the call made inside execute",
              meta["tool_names"] == ["mobile-mcp.mobile_list_elements_on_screen"] and meta["mcp_direct_calls"] == 0, meta)


def test_run_one_old_opencode_leaves_unit_pending():
    with tempfile.TemporaryDirectory() as results, fresh(NO_PRELOAD), harness(results):
        spawned = []
        with patched(isolation, run_opencode=lambda *a, **k: spawned.append(a) or (0, "", False)), \
                patched(sim_device, clone_golden=lambda tid: spawned.append("clone") or "CLONE"), \
                patched(bench, detect_opencode_version=lambda: "1.19.6"):
            try:
                bench.run_one("gpt_low", "none", TASK)
                check("run_one raises on an opencode 1.x", False)
            except bench.OpencodeVersionError as e:
                check("run_one raises on an opencode 1.x", "1.19.6" in str(e), e)
        check("no device or agent was touched", spawned == [], spawned)
        check("nothing was written for the unit", not os.path.exists(os.path.join(results, "gpt_low__none")))
        check("the unit stays pending", ledger.needs_run(results, "gpt_low", "none", "bsky-01", bench.HARNESS))


def retry(ts, due):
    return json.dumps({"type": "retry", "timestamp": ts, "nextRetryAt": due, "attempt": 2}) + "\n"


def step(ts):
    return json.dumps({"type": "step_start", "timestamp": ts}) + "\n"


def retries_of(*lines):
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        f.write("".join(lines))
    try:
        return bench.provider_retries(f.name)
    finally:
        os.unlink(f.name)


def test_provider_retries():
    check("a run with no retries lost nothing", retries_of(step(0), tool_use("shell"), step(5000)) == (0, 0.0))
    got = retries_of(step(1000), retry(1000, 3000), retry(4000, 9000), tool_use("shell"), step(20000))
    check("a step's retries cost the time from its start to the last retry being due", got == (2, 8.0), got)
    got = retries_of(step(0), retry(100, 2000), step(10000), tool_use("shell"), step(20000), retry(20500, 26000))
    check("steps add up, a trailing one included", got == (2, 8.0), got)
    got = retries_of(retry(500, 1500), "not json\n")
    check("a retry before any step counts from its own time", got == (1, 1.0), got)
    check("a missing transcript lost nothing", bench.provider_retries("/nonexistent/t.jsonl") == (0, 0.0))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{PASS} checks passed")
