#!/usr/bin/env python3
"""Local unit tests for preload.py and its wiring into bench.run_one / run_matrix - no devices, no
network, no API spend. Tool entries are fakes whose steps write under a tempdir.

    python3 runner/test_preload.py
"""
import contextlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench
import isolation
import ledger
import preload
import report_data
import run_matrix
import sim_device

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


@contextlib.contextmanager
def patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


@contextlib.contextmanager
def environ(**kv):
    """Set process environment variables (None unsets), for this process and the steps it spawns."""
    old = {k: os.environ.get(k) for k in kv}
    def put(vals):
        for k, v in vals.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    put(kv)
    try:
        yield
    finally:
        put(old)


@contextlib.contextmanager
def fresh(entries):
    """A PRELOAD table holding only `entries`, with the per-process memory cleared around it."""
    with patched(preload, PRELOAD=entries, _built=set(), _failed={}):
        yield


def counting_entry(d, check_ok=True, step_rc=0):
    """An entry whose one step appends to <d>/count and creates <d>/artifact; its check tests the
    artifact (or always fails, when check_ok is False)."""
    art, cnt = os.path.join(d, "artifact"), os.path.join(d, "count")
    script = f"echo step-output; echo x >> {cnt}; touch {art}; exit {step_rc}"
    return {"why": "test", "steps": [{"argv": ["sh", "-c", script], "timeout": 10}],
            "check": ["test", "-e", art] if check_ok else ["false"]}


def runs(d):
    try:
        return len(open(os.path.join(d, "count")).read().split())
    except FileNotFoundError:
        return 0


def test_table():
    check("every benchmarked tool has an entry", set(bench.TOOLS) <= set(preload.PRELOAD),
          f"missing {set(bench.TOOLS) - set(preload.PRELOAD)}")
    for tool, spec in preload.PRELOAD.items():
        check(f"{tool}: states why", bool(spec.get("why", "").strip()))
        if spec["steps"]:
            check(f"{tool}: steps come with a check", bool(spec.get("check")))
            check(f"{tool}: every step has a timeout", all(s.get("timeout") for s in spec["steps"]))


def test_build_then_present():
    with tempfile.TemporaryDirectory() as d, fresh({"fake": counting_entry(d)}):
        rec = preload.ensure("fake@1.0.0")
        check("first call builds", rec["state"] == "built", rec)
        check("the step ran once", runs(d) == 1, runs(d))
        check("the step is audited with its rc", [s["rc"] for s in rec["steps"]] == [0], rec)
        rec = preload.ensure("fake@1.0.0")
        check("second call finds it present", rec["state"] == "present", rec)
        check("the step did not run again", runs(d) == 1, runs(d))


def test_present_without_building():
    with tempfile.TemporaryDirectory() as d, fresh({"fake": counting_entry(d)}):
        open(os.path.join(d, "artifact"), "w").close()
        rec = preload.ensure("fake@1.0.0")
        check("an artifact already in place is not rebuilt", rec["state"] == "present" and runs(d) == 0, rec)


def test_once_per_version():
    with tempfile.TemporaryDirectory() as d, fresh({"fake": counting_entry(d)}):
        preload.ensure("fake@1.0.0")
        os.remove(os.path.join(d, "artifact"))
        try:
            preload.ensure("fake@1.0.0")
            check("an artifact that vanishes after its build is fatal", False)
        except preload.PreloadError as e:
            check("an artifact that vanishes after its build is fatal", "vanished" in str(e), e)
        check("... without building a second time", runs(d) == 1, runs(d))
        rec = preload.ensure("fake@2.0.0")
        check("another version of the same tool builds for itself", rec["state"] == "built" and runs(d) == 2,
              (rec, runs(d)))


def test_check_failure_is_fatal():
    with tempfile.TemporaryDirectory() as d, fresh({"fake": counting_entry(d, check_ok=False)}):
        try:
            preload.ensure("fake@1.0.0")
            check("steps that leave no artifact are fatal", False)
        except preload.PreloadError as e:
            check("steps that leave no artifact are fatal", "still fails" in str(e), e)
        try:
            preload.ensure("fake@1.0.0")
            check("the failure is remembered", False)
        except preload.PreloadError:
            check("the failure is remembered", runs(d) == 1, runs(d))


def test_step_failure_is_fatal_with_output():
    with tempfile.TemporaryDirectory() as d, fresh({"fake": counting_entry(d, step_rc=3)}):
        try:
            preload.ensure("fake@1.0.0")
            check("a failing step is fatal", False)
        except preload.PreloadError as e:
            check("a failing step is fatal, naming its rc", "rc=3" in str(e), e)
            check("... and carrying its output tail", "step-output" in str(e), e)
            first = str(e)
        try:
            preload.ensure("fake@1.0.0")
            check("a later call repeats the original failure", False, "no error")
        except preload.PreloadError as e:
            check("a later call repeats the original failure", str(e) == first, e)
        check("... without running the step again", runs(d) == 1, runs(d))


def test_step_timeout_is_fatal():
    entry = {"why": "test", "steps": [{"argv": ["sleep", "5"], "timeout": 1}], "check": ["false"]}
    with fresh({"fake": entry}):
        try:
            preload.ensure("fake@1.0.0")
            check("an overrunning step is fatal", False)
        except preload.PreloadError as e:
            check("an overrunning step is fatal", "rc=-9" in str(e) and "TIMEOUT" in str(e), e)


def test_malformed_entries():
    with fresh({"nocheck": {"why": "test", "steps": [{"argv": ["true"], "timeout": 1}], "check": None}}):
        for variant, needle in (("unlisted@1", "no PRELOAD entry"), ("nocheck@1", "no check")):
            try:
                preload.ensure(variant)
                check(f"{variant} is refused", False)
            except preload.PreloadError as e:
                check(f"{variant} is refused", needle in str(e), e)


def test_empty_entry():
    with fresh({"fake": {"why": "nothing", "steps": [], "check": None}}):
        check("an empty entry records none", preload.ensure("fake") == {"state": "none", "s": 0.0})


def test_agent_device_bundle_guard():
    """The real agent-device entry, run against fake installs: each is refused by name, before any of
    the tool's runner code runs."""
    verified = json.load(open(os.path.join(bench.CONFIGS, "tool-versions.json")))["agent-device"]
    host, owner = "dist/src/runner-client.js", "dist/src/runner-owner-facade.js"
    bound = {host: "export {};\n", owner: "export function setAppleRunnerHostEnvironment() {}\n"}
    empty = {f"dist/src/runner-{name}.js": "export {};\n" for name in ("disposal", "cache-metadata", "lease")}
    lookalikes = dict(empty, **{"dist/src/runner-disposal.js":
                                "export const a = () => 'forceRunnerXctestrunRebuild', "
                                "b = () => 'forceRunnerXctestrunRebuild';\n"})
    cases = (("another version", "0.22.0", {}, "has not been verified for 0.22.0"),
             ("no bundle", verified, {}, f"cannot load {host}"),
             ("a bundle that binds no host environment", verified, {host: "export {};\n", owner: "export {};\n"},
              f"differs from the verified {verified} bundle"),
             ("a bundle without the build function", verified, {**bound, **empty},
              "0 exports of dist/src/runner-disposal.js look like ensureXctestrunArtifact"),
             ("a bundle where two functions look like it", verified, {**bound, **lookalikes},
              "2 exports of dist/src/runner-disposal.js look like ensureXctestrunArtifact"))
    check("the preload is verified for the pinned agent-device", f"const VERSION = '{verified}';" in
          open(os.path.join(os.path.dirname(os.path.abspath(preload.__file__)), "preload-agent-device.mjs")).read())
    for label, version, files, needle in cases:
        with tempfile.TemporaryDirectory() as d, fresh({"agent-device": preload.PRELOAD["agent-device"]}), \
                patched(preload, PLACEHOLDERS={**preload.PLACEHOLDERS, "agent_device_dir": lambda: d,
                                               "build_sim": lambda: "SIM"}):
            json.dump({"version": version, "type": "module"}, open(os.path.join(d, "package.json"), "w"))
            for rel, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
                open(os.path.join(d, rel), "w").write(text)
            try:
                preload.ensure(f"agent-device@{version}")
                check(f"agent-device with {label} is refused", False)
            except preload.PreloadError as e:
                check(f"agent-device with {label} is refused", needle in str(e) and "rc=2" in str(e), e)


IOS = "com.apple.CoreSimulator.SimRuntime.iOS-18-5"


def sim(name, udid, runtime=IOS):
    return {"name": name, "udid": udid, "state": "Shutdown", "runtime": runtime}


def destination(devices, env=None):
    """sim_device.build_destination() over a fake device list: the udid, or the DeviceError text."""
    with patched(sim_device, _devices=lambda: devices, load_manifest=lambda: {"name": "golden-renamed"}), \
            environ(BENCH_PRELOAD_SIM=(env or {}).get("BENCH_PRELOAD_SIM")):
        try:
            return sim_device.build_destination()
        except sim_device.DeviceError as e:
            return f"DeviceError: {e}"


def test_build_destination():
    bench_owned = [sim("golden-renamed", "1"), sim("bench-golden-v1", "2"), sim("bench-run-1-bsky-01", "3")]
    check("the first iOS simulator the bench does not own is named",
          destination(bench_owned + [sim("tv", "4", "com.apple.CoreSimulator.SimRuntime.tvOS-18-5"),
                                     sim("iPhone-dev", "6"), sim("other", "5")]) == "5")
    got = destination(bench_owned)
    check("the golden and bench-* devices are never named, even when nothing else exists",
          got.startswith("DeviceError: no available iOS simulator besides the golden"), got)
    for udid in ("1", "3"):
        got = destination(bench_owned + [sim("other", "5")], {"BENCH_PRELOAD_SIM": udid})
        check(f"BENCH_PRELOAD_SIM naming bench device {udid} is refused", "a bench device" in got, got)
    check("BENCH_PRELOAD_SIM names another simulator",
          destination(bench_owned + [sim("other", "5")], {"BENCH_PRELOAD_SIM": "5"}) == "5")


def test_destination_failure_is_a_preload_failure():
    entry = {"why": "test", "steps": [{"argv": ["echo", "{build_sim}"], "timeout": 5}], "check": ["false"]}
    with fresh({"fake": entry}), patched(sim_device, _devices=lambda: [sim("bench-run-x", "1")]):
        try:
            preload.ensure("fake@1")
            check("no destination is a PreloadError", False)
        except preload.PreloadError as e:
            check("no destination is a PreloadError", "no available iOS simulator" in str(e), e)


FAKE_AX = "echo 'dyld[1]: DYLD_ROOT_PATH not set for simulator program' >&2; kill -ABRT $$"


def fake_argent(d, ss="echo " + "a" * 64, ax=FAKE_AX):
    b = os.path.join(d, "pkg", "bin", "darwin")
    os.makedirs(b)
    for name, body in (("simulator-server", ss), ("ax-service", ax)):
        with open(os.path.join(b, name), "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(os.path.join(b, name), 0o755)
    return os.path.join(d, "pkg")


def argent_ensure(d, pkg):
    with fresh({"argent": preload.PRELOAD["argent"]}), \
            patched(preload, PLACEHOLDERS={**preload.PLACEHOLDERS, "argent_pkg": lambda: pkg}), \
            environ(HOME=os.path.join(d, "home")):
        return preload.ensure("argent@0.15.0")


def test_argent_entry():
    """The real argent entry against a fake install whose binaries behave like 0.15.0's."""
    with tempfile.TemporaryDirectory() as d:
        pkg = fake_argent(d)
        check("argent: first call launches the binaries", argent_ensure(d, pkg)["state"] == "built")
        check("argent: second call finds the record", argent_ensure(d, pkg)["state"] == "present")
        home = os.path.join(d, "home")
        # A system python (Xcode's /usr/bin/python3) creates ~/Library/Caches under any HOME it runs with.
        check("argent: the record is under HOME", [e for e in os.listdir(home) if e != "Library"] == [".cache"],
              os.listdir(home))
        os.utime(os.path.join(pkg, "bin", "darwin", "simulator-server"), (1, 1))
        check("argent: a changed binary is launched again", argent_ensure(d, pkg)["state"] == "built")
    for label, kw, needle in (
            ("an ax-service that runs on the host", {"ax": "exit 0"}, "unexpected rc=0"),
            ("a simulator-server without a fingerprint", {"ss": "echo nope"}, "unexpected rc=0: nope")):
        with tempfile.TemporaryDirectory() as d:
            try:
                argent_ensure(d, fake_argent(d, **kw))
                check(f"argent: {label} is fatal", False)
            except preload.PreloadError as e:
                check(f"argent: {label} is fatal", needle in str(e), e)


def fake_mobilecli(d, body="echo 'mobilecli version 9.9.9'"):
    path = os.path.join(d, "mobilecli")
    with open(path, "w") as f:
        f.write(f"#!/bin/sh\n{body}\n")
    os.chmod(path, 0o755)
    return path


def mobile_mcp_ensure(d, binary):
    with fresh({"mobile-mcp": preload.PRELOAD["mobile-mcp"]}), \
            patched(preload, PLACEHOLDERS={**preload.PLACEHOLDERS, "mobilecli": lambda: binary}), \
            environ(HOME=os.path.join(d, "home")):
        return preload.ensure("mobile-mcp@1.0.17")


def test_mobile_mcp_entry():
    """The real mobile-mcp entry against a fake mobilecli."""
    with tempfile.TemporaryDirectory() as d:
        binary = fake_mobilecli(d)
        check("mobile-mcp: first call launches mobilecli", mobile_mcp_ensure(d, binary)["state"] == "built")
        check("mobile-mcp: second call finds the record", mobile_mcp_ensure(d, binary)["state"] == "present")
        os.utime(binary, (1, 1))
        check("mobile-mcp: a changed binary is launched again", mobile_mcp_ensure(d, binary)["state"] == "built")
    for label, binary_of, needle in (
            ("a binary that is not mobilecli", lambda d: fake_mobilecli(d, "echo nope"), "unexpected rc=0: nope"),
            ("a missing binary", lambda d: os.path.join(d, "absent"), "mobilecli binary missing")):
        with tempfile.TemporaryDirectory() as d:
            try:
                mobile_mcp_ensure(d, binary_of(d))
                check(f"mobile-mcp: {label} is fatal", False)
            except preload.PreloadError as e:
                check(f"mobile-mcp: {label} is fatal", needle in str(e), e)


TASK = {"id": "bsky-01", "app": "bluesky", "kind": "k", "needs_auth": False, "prompt": "p"}


@contextlib.contextmanager
def harness(results):
    """bench.run_one with every device / process / network touchpoint replaced by a no-op, so the
    code between them - preload, meta.json, env.json, the ledger - runs for real."""
    def fake_opencode(cmd, cwd, timeout, stdout_file, env=None):
        stdout_file.write(json.dumps({"type": "tool_use", "part": {"tool": "bash"}}) + "\n")
        return 0, "", False

    def fake_config(tool, udid):
        d = tempfile.mkdtemp(prefix="test-preload-cfg-")
        open(os.path.join(d, "opencode.json"), "w").write("{}")
        return d

    with contextlib.ExitStack() as st:
        st.enter_context(patched(bench, RESULTS=results, model_runnable=lambda *a: True,
                                 ollama_served_cached=lambda: set(), reset_hook=lambda *a, **k: None,
                                 reset_app=lambda *a: None, screenshot=lambda *a: None,
                                 _versions_cache={}, detect_opencode_version=lambda: "2.3.0"))
        st.enter_context(patched(isolation, teardown_all=lambda *a, **k: {}, start_proxies=lambda *a, **k: {},
                                 run_opencode=fake_opencode, write_env_manifest=isolation.write_env_manifest))
        st.enter_context(patched(sim_device, reap_stale_clones=lambda: None,
                                 check_golden=lambda: {"name": "g", "udid": "G"},
                                 refresh_golden_sessions=lambda: None,
                                 clone_golden=lambda tid: "CLONE", boot=lambda u: None, destroy=lambda u: True))
        st.enter_context(patched(bench.gen_configs, write_run_config=fake_config))
        st.enter_context(patched(bench.bench_env, none_shim_dir=lambda udid=None: results))
        # an empty HOME: the operator's own opencode config would otherwise refuse every run (ambient.py)
        st.enter_context(environ(HOME=os.path.join(results, "home"), XDG_CONFIG_HOME=None, XDG_DATA_HOME=None))
        yield


def test_run_one_records_preload():
    with tempfile.TemporaryDirectory() as results, tempfile.TemporaryDirectory() as d, \
            fresh({"none": counting_entry(d)}), harness(results):
        bench.run_one("gpt_low", "none", TASK)
        rdir = os.path.join(results, "gpt_low__none", "bsky-01")
        meta = json.load(open(os.path.join(rdir, "meta.json")))
        env = json.load(open(os.path.join(rdir, "env.json")))
        check("run_one preloads before the run", runs(d) == 1, runs(d))
        pre = meta["versions"].get("preload") or {}
        check("meta.json versions.preload records the build", pre.get("state") == "built", meta["versions"])
        check("... with its duration", isinstance(pre.get("s"), float), pre)
        check("env.json records the full preload", env.get("preload", {}).get("steps", [{}])[0].get("rc") == 0,
              env.get("preload"))
        check("the rest of versions is unchanged",
              {k: v for k, v in meta["versions"].items() if k != "preload"} == bench._versions_cache[("none", "bluesky")],
              meta["versions"])
        open(os.path.join(rdir, "final.png"), "wb").write(b"\x89PNG\r\n\x1a\n")   # screenshot() is stubbed
        check("the ledger reads the stamped run as done at the current harness",
              not ledger.needs_run(results, "gpt_low", "none", "bsky-01", bench.HARNESS))
        row = report_data.collect(results)[("gpt_low", "none", "bsky-01")]
        check("the exporter does not read it as polluted", row["polluted"] is False, row.get("polluted"))
        bench.run_one("gpt_low", "none", dict(TASK, id="bsky-02"))
        meta2 = json.load(open(os.path.join(results, "gpt_low__none", "bsky-02", "meta.json")))
        check("the next run finds it present", meta2["versions"]["preload"]["state"] == "present",
              meta2["versions"])
        check("... without rebuilding", runs(d) == 1, runs(d))


def test_run_one_surface_error_records_preload():
    def no_clone(tid):
        raise sim_device.DeviceError("clone failed")
    with tempfile.TemporaryDirectory() as results, tempfile.TemporaryDirectory() as d, \
            fresh({"none": counting_entry(d)}), harness(results), patched(sim_device, clone_golden=no_clone):
        bench.run_one("gpt_low", "none", TASK)
        meta = json.load(open(os.path.join(results, "gpt_low__none", "bsky-01", "meta.json")))
        check("a surface-error meta carries versions.preload too",
              meta["returncode"] == -2 and meta["versions"].get("preload", {}).get("state") == "built", meta)


def test_run_one_preload_failure_leaves_unit_pending():
    with tempfile.TemporaryDirectory() as results, tempfile.TemporaryDirectory() as d, \
            fresh({"none": counting_entry(d, check_ok=False)}), harness(results):
        spawned = []
        with patched(isolation, run_opencode=lambda *a, **k: spawned.append(a) or (0, "", False)):
            try:
                bench.run_one("gpt_low", "none", TASK)
                check("run_one raises on a preload failure", False)
            except preload.PreloadError:
                check("run_one raises on a preload failure", True)
        check("no agent was spawned", spawned == [], spawned)
        check("nothing was written for the unit", not os.path.exists(os.path.join(results, "gpt_low__none")))
        check("the unit stays pending",
              ledger.needs_run(results, "gpt_low", "none", "bsky-01", bench.HARNESS))


def test_matrix_stops_a_tool_on_preload_failure():
    """One PreloadError ends that work item: the tool's remaining units are not attempted."""
    calls = []

    def fake_run_one(key, tool, t, force=False):
        calls.append((tool, t["id"]))
        if tool == "none":
            raise preload.PreloadError("boom")

    tasks = [dict(TASK, id=f"bsky-0{i}") for i in (1, 2, 3)]
    env = {"ONLY": bench.EDITION["models"][0], "TOOLS": "none,argent", "APPS": "bluesky"}
    old_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        with tempfile.TemporaryDirectory() as results, \
                patched(bench, RESULTS=results, run_one=fake_run_one, load_tasks=lambda: tasks,
                        tool_variant=lambda tool: tool), \
                patched(isolation, acquire_lock=lambda **k: None), \
                patched(sim_device, check_golden=lambda: {}):
            run_matrix.main()
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("the failing tool is tried once, then skipped", [c for c in calls if c[0] == "none"] == [("none", "bsky-01")],
          calls)
    check("the other tool still runs every unit", [c for c in calls if c[0] == "argent"] ==
          [("argent", t["id"]) for t in tasks], calls)


def test_bench_cli_stops_a_cell_on_preload_failure():
    calls = []

    def fake_run_one(mk, tool, t, force=False):
        calls.append(t["id"])
        raise preload.PreloadError("boom")

    tasks = [dict(TASK, id=f"bsky-0{i}") for i in (1, 2, 3)]
    with patched(bench, run_one=fake_run_one, load_tasks=lambda: tasks, check_tool_versions=lambda: None,
                 check_app_versions=lambda: None, ollama_served_tags=lambda: set(),
                 model_runnable=lambda *a: True), \
            patched(isolation, acquire_lock=lambda **k: None), \
            patched(sim_device, check_golden=lambda: {}), \
            patched(sys, argv=["bench.py", "--cell", "gpt_low:none"]):
        bench.main()
    check("bench.py tries a failing cell once, then skips it", calls == ["bsky-01"], calls)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{PASS} checks passed")
