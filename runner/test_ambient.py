#!/usr/bin/env python3
"""Local unit tests for ambient.py and its wiring into bench.run_one / run_matrix / doctor - no devices,
no network, no API spend. Every host is a synthetic HOME under tempfile.

    python3 runner/test_ambient.py
"""
import contextlib
import json
import os
import plistlib
import pwd
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ambient
import bench
import doctor
import isolation
import ledger
import run_matrix
import sim_device
from test_preload import TASK, environ, fresh, harness, patched

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


def put(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


class Host:
    """A synthetic host: HOME, a run dir (the per-run config dir, holding the argent template plus a
    staged rule) under its own tmp, and empty managed-config locations."""

    def __init__(self, d):
        self.d = d
        self.home = os.path.join(d, "home")
        self.config = os.path.join(self.home, ".config", "opencode")
        self.tmp = os.path.join(d, "tmp")
        self.run = os.path.join(self.tmp, "bench-cfg-argent-x")
        self.rule = put(os.path.join(self.run, "always-on-rule.md"), "rule\n")
        cfg = json.load(open(os.path.join(ambient.CONFIGS, "argent", "opencode.json")))
        self.cfg = dict(cfg, instructions=[self.rule])
        put(os.path.join(self.run, "opencode.json"), json.dumps(self.cfg))
        self.env = {"HOME": self.home}
        self.managed = os.path.join(d, "managed")
        self.prefs = os.path.join(d, "prefs")

    def scan(self, **env):
        return ambient.scan(self.run, self.cfg, {**self.env, **env})


@contextlib.contextmanager
def host():
    with tempfile.TemporaryDirectory() as d:
        h = Host(os.path.realpath(d))
        with patched(ambient, MANAGED_DIR=h.managed, MANAGED_PREFS=h.prefs):
            yield h


@contextlib.contextmanager
def tmpdir(path):
    """tempfile.gettempdir() answering `path`, where gen_configs.write_run_config puts a run dir."""
    with patched(tempfile, tempdir=path):
        yield


def srcs(found):
    return [src for src, _ in found]


def test_run_env():
    check("RUN_ENV switches off the Claude Code directories",
          ambient.RUN_ENV["OPENCODE_DISABLE_CLAUDE_CODE"] == ambient.RUN_ENV["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] == "1")
    blank = {k for k, v in ambient.RUN_ENV.items() if v == ""}
    check("RUN_ENV blanks the launcher's config hooks",
          blank == {"OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR",
                    "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_CONFIG_PROJECT_DISABLE", "OPENCODE_FLAGS",
                    "OPENCODE_SIMULATE"}, blank)
    check("... but not the two opencode 2 takes literally when empty",
          not {"OPENCODE_TEST_HOME", "OPENCODE_DB"} & set(ambient.RUN_ENV))
    check("... and sets none that opencode 2 dropped",
          not {"OPENCODE_PURE", "OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"}
          & set(ambient.RUN_ENV))


def test_templates_carry_the_config_switches():
    for tool in bench.TOOLS:
        cfg = json.load(open(os.path.join(ambient.CONFIGS, tool, "opencode.json")))
        check(f"{tool}: the template removes every host plugin",
              not ambient._plugin_on(cfg.get("plugins"), ambient.HOST_PLUGIN), cfg.get("plugins"))
        check(f"{tool}: ... and the .claude/.agents skill loader",
              not ambient._plugin_on(cfg.get("plugins"), ambient.COMPATIBILITY_PLUGIN), cfg.get("plugins"))
        check(f"{tool}: ... but keeps opencode's other built-ins",
              ambient._plugin_on(cfg.get("plugins"), "opencode.skill"), cfg.get("plugins"))
        check(f"{tool}: the template allows every tool and turns web search off",
              cfg.get("permission") == "allow" and cfg.get("websearch") is False, cfg)
        mcp = ambient._mcp_servers(cfg.get("mcp"))
        check(f"{tool}: host servers are disabled with whole entries",
              all(ambient._mcp_off(mcp[name]) for name in ("radon", "linear")), mcp)


def test_plugin_list():
    on = ambient._plugin_on
    check("no list leaves a plugin on", on(None, "x") and on([], "x"))
    check("-id, -prefix.* and -* turn it off", not on(["-x"], "x") and not on(["-a.*"], "a.b") and not on(["-*"], "x"))
    check("a prefix does not match a longer name", on(["-a.*"], "ab.c"))
    check("a later entry wins", on(["-*", "a.*"], "a.b") and not on(["-*", "a.*", "-a.b"], "a.b"))
    check("a package entry is not a switch", on([{"package": "./p"}], "x"))


def test_clean_host():
    with host() as h:
        r = h.scan()
        check("a clean host loads exactly the run's staged rule", r["instructions"] == [h.rule], r)
        check("... and nothing is ambient or neutralised", r["ambient"] == [] and r["neutralised"] == [], r)


def test_files_opencode_2_no_longer_reads():
    with host() as h:
        put(os.path.join(h.home, ".claude", "CLAUDE.md"), "MARKER-CLAUDE")
        put(os.path.join(h.tmp, "CLAUDE.md"), "MARKER-CLAUDE")
        put(os.path.join(h.tmp, "CONTEXT.md"))
        put(os.path.join(h.home, ".opencode", "skill", "u", "SKILL.md"))
        put(os.path.join(h.home, ".opencode", "opencode.json"), json.dumps({"instructions": ["/x.md"]}))
        put(os.path.join(h.config, "config.json"), json.dumps({"instructions": ["/x.md"]}))
        r = h.scan()
        check("CLAUDE.md, CONTEXT.md, ~/.opencode and config.json are not sources",
              r == {"instructions": [h.rule], "neutralised": [], "ambient": []}, r)


def test_global_agents_md():
    with host() as h:
        p = put(os.path.join(h.config, "AGENTS.md"), "MARKER-AGENTS")
        r = h.scan()
        check("<config>/AGENTS.md loads ahead of the configured rule", r["instructions"] == [p, h.rule], r)
        check("... and is refused", srcs(r["ambient"]) == [p], r["ambient"])
        xdg = os.path.join(h.d, "xdg")
        q = put(os.path.join(xdg, "opencode", "AGENTS.md"))
        r = h.scan(XDG_CONFIG_HOME=xdg)
        check("$XDG_CONFIG_HOME moves <config>", srcs(r["ambient"]) == [q], r["ambient"])


def test_project_files():
    with host() as h:
        near = put(os.path.join(h.tmp, "AGENTS.md"))
        top = put(os.path.join(h.d, "AGENTS.md"))
        r = h.scan()
        check("an AGENTS.md above a run dir outside any repository is not loaded",
              r["instructions"] == [h.rule] and r["ambient"] == [], r)
        os.makedirs(os.path.join(h.d, ".git"))
        r = h.scan()
        check("a .git above the run dir makes it the project root, and every AGENTS.md up to it loads",
              r["instructions"] == [near, top, h.rule] and srcs(r["ambient"]) == [near, top], r)
    with host() as h:
        near = put(os.path.join(h.tmp, "AGENTS.md"))
        top = put(os.path.join(h.d, "AGENTS.md"))
        put(os.path.join(os.path.dirname(h.d), "never.md"))
        r = h.scan(HOME=h.d)
        check("a run dir under the home dir walks up to it",
              r["instructions"] == [near, top, h.rule] and srcs(r["ambient"]) == [near, top], r)


def test_global_config_keys():
    with host() as h:
        instr = put(os.path.join(h.home, "mine.md"))
        cfg = put(os.path.join(h.config, "opencode.json"), json.dumps({
            "$schema": "x", "provider": {"ollama": {"options": {"baseURL": "http://h/v1"}}},
            "providers": {"o": {}}, "model": "a/b", "small_model": "a/c", "command": {"c": {"template": "t"}},
            "instructions": ["~/mine.md"], "permission": {"bash": "deny"}, "tools": {"skill": False},
            "plugin": ["p"], "websearch": True, "experimental": {"todo": True},
            "mcp": {"radon": {"type": "local", "command": ["x"]}, "mine": {"type": "local", "command": ["y"]}},
            "agent": {"build": {"prompt": "p"}}}))
        r = h.scan()
        check("behaviour keys the run cannot override are refused, by key",
              [why.split("`")[1] for _, why in r["ambient"]] == ["instructions", "experimental", "mine", "agent"],
              r["ambient"])
        check("... all named against the file", set(srcs(r["ambient"])) == {cfg}, r["ambient"])
        check("permission rules the run outvotes, plugins it removes, a key it replaces and a server it "
              "disables are neutralised",
              [why for _, why in r["neutralised"]] ==
              ['permission: outvoted by the run\'s `permission: "allow"`',
               'tools: outvoted by the run\'s `permission: "allow"`', "plugin: removed by the run's `plugins`",
               "websearch: replaced by the run's config", "mcp.radon: disabled by the run's config"],
              r["neutralised"])
        check("host instructions load before the run's", r["instructions"] == [instr, h.rule], r["instructions"])


def test_native_config_format():
    with host() as h:
        put(os.path.join(h.config, "opencode.json"), json.dumps({
            "permissions": [{"action": "shell", "resource": "*", "effect": "deny"}], "plugins": ["p"],
            "mcp": {"servers": {"linear": {"type": "remote", "url": "u"}, "mine": {"type": "remote", "url": "u"}},
                    "timeout": {"catalog": 1}},
            "agents": {"x": {}}, "policies": [], "skills": ["./s"]}))
        r = h.scan()
        check("a 2.x-format host config is vetted the same way",
              [why.split("`")[1] for _, why in r["ambient"]] == ["mine", "mcp.timeout", "agents", "policies", "skills"],
              r["ambient"])
        check("... and neutralised the same way",
              [why.split(":")[0] for _, why in r["neutralised"]] == ["permissions", "plugins", "mcp.linear"],
              r["neutralised"])


def test_run_config_must_carry_the_switches():
    with host() as h:
        put(os.path.join(h.config, "opencode.json"), json.dumps({
            "permission": {"bash": "deny"}, "plugin": ["p"], "mcp": {"radon": {"type": "local", "command": ["x"]}}}))
        put(os.path.join(h.config, "plugins", "p.ts"))
        skills = os.path.join(h.home, ".agents", "skills")
        put(os.path.join(skills, "s", "SKILL.md"))
        bare = {"mcp": {"radon": {"enabled": False}}, "instructions": [h.rule]}
        r = ambient.scan(h.run, bare, h.env)
        check("without the template's switches, the same host sources are refused",
              [(os.path.relpath(s, h.home), why.split(" ")[0]) for s, why in r["ambient"]] ==
              [(".config/opencode/opencode.json", "`permission`"), (".config/opencode/opencode.json", "`plugin`"),
               (".config/opencode/opencode.json", "MCP"), (".config/opencode/plugins", "plugins"),
               (".agents/skills", "1")], r["ambient"])
        check("... a bare {\"enabled\": false} does not disable a host server on opencode 2",
              any("`radon`" in why for _, why in r["ambient"]), r["ambient"])


def test_late_config_wins():
    with host() as h:
        late = put(os.path.join(h.tmp, ".opencode", "opencode.json"),
                   json.dumps({"permission": {"bash": "deny"}, "mcp": {"radon": {"type": "local", "command": ["x"]}},
                               "plugins": ["p"], "websearch": True}))
        r = h.scan()
        check("a .opencode/opencode.json above the run dir loads after the run's: nothing in it is overridable",
              [(s, why.split("`")[1]) for s, why in r["ambient"]] ==
              [(late, "permission"), (late, "mcp"), (late, "plugins"), (late, "websearch")], r["ambient"])
        early = put(os.path.join(h.tmp, "opencode.json"), json.dumps({"websearch": True, "shell": "/bin/zsh"}))
        r = h.scan()
        check("an opencode.json above the run dir loads before it: a key the run sets is replaced, another is not",
              (early, "websearch: replaced by the run's config") in r["neutralised"]
              and [why.split("`")[1] for s, why in r["ambient"] if s == early] == ["shell"], r)


def test_jsonc():
    with host() as h:
        put(os.path.join(h.config, "opencode.jsonc"),
            '{\n  // a comment\n  "provider": {"x": {"options": {"baseURL": "http://a//b/*c*/"}}}, /* block */\n'
            '  "instructions": ["~/i.md",],\n}\n')
        r = h.scan()
        check("opencode.jsonc with comments and trailing commas is vetted",
              [why.split("`")[1] for _, why in r["ambient"]] == ["instructions"], r["ambient"])
        check("... with // and /* inside a string left alone",
              ambient._jsonc(open(os.path.join(h.config, "opencode.jsonc")).read())["provider"]["x"]["options"]
              == {"baseURL": "http://a//b/*c*/"})
        bad = put(os.path.join(h.config, "opencode.json"), "{ nope")
        r = h.scan()
        check("an unreadable config is refused rather than trusted",
              any(s == bad and "cannot be read" in why for s, why in r["ambient"]), r["ambient"])


def test_config_dirs():
    with host() as h:
        put(os.path.join(h.config, "skills", "s", "SKILL.md"))
        put(os.path.join(h.config, "skills", "t", "SKILL.md"))
        put(os.path.join(h.config, "skills", "top.md"))
        put(os.path.join(h.config, "skills", "t", "notes.md"))
        put(os.path.join(h.config, "agent", "deep", "a.md"))
        put(os.path.join(h.config, "modes", "build.md"))
        put(os.path.join(h.config, "modes", "nested", "ignored.md"))
        put(os.path.join(h.config, "mode", "nested", "ignored.md"))
        put(os.path.join(h.config, "commands", "c.md"))
        put(os.path.join(h.config, "plugins", "p.js"))
        put(os.path.join(h.tmp, ".opencode", "agents", "b.md"))
        os.makedirs(os.path.join(h.tmp, ".opencode", "plugins", "package"))
        put(os.path.join(h.managed, "skills", "m", "SKILL.md"))
        put(os.path.join(h.run, ".opencode", "skills", "own", "SKILL.md"))
        put(os.path.join(h.run, ".opencode", "agents", "own.md"))
        r = h.scan()
        got = [(os.path.relpath(s, h.d), why.split(" loaded")[0]) for s, why in r["ambient"]]
        check("host agents, modes and skills are refused per dir, with a count",
              got == [("home/.config/opencode/agent", "1 agent definition(s)"),
                      ("home/.config/opencode/modes", "1 mode definition(s)"),
                      ("home/.config/opencode/skills", "3 skill definition(s)"),
                      ("tmp/.opencode/agents", "1 agent definition(s)"),
                      ("managed/skills", "1 skill definition(s)")], got)
        check("host plugins, files or packages, are neutralised",
              r["neutralised"] == [(os.path.join(h.config, "plugins"), "plugins: removed by the run's `plugins`"),
                                   (os.path.join(h.tmp, ".opencode", "plugins"),
                                    "plugins: removed by the run's `plugins`")], r["neutralised"])


def test_other_agents_skills():
    with host() as h:
        claude = os.path.join(h.home, ".claude", "skills")
        agents = os.path.join(h.home, ".agents", "skills")
        above = os.path.join(h.tmp, ".agents", "skills")
        for d in (claude, agents, above):
            put(os.path.join(d, "s", "SKILL.md"))
        r = h.scan()
        check("~/.claude, ~/.agents and .agents above the run dir are neutralised, each by its switch",
              r["ambient"] == [] and r["neutralised"] ==
              [(claude, "skills: OPENCODE_DISABLE_CLAUDE_CODE"),
               (agents, "skills: the run's `plugins` drops opencode.config.compatibility"),
               (above, "skills: the run's `plugins` drops opencode.config.compatibility")], r)


def test_opencode_home():
    with host() as h:
        r = h.scan(OPENCODE_TEST_HOME="")
        check("an empty OPENCODE_TEST_HOME is refused", srcs(r["ambient"]) == ["$OPENCODE_TEST_HOME"], r["ambient"])
        other = os.path.join(h.d, "other")
        skills = os.path.join(other, ".agents", "skills")
        put(os.path.join(skills, "s", "SKILL.md"))
        put(os.path.join(h.home, ".agents", "skills", "s", "SKILL.md"))
        r = h.scan(OPENCODE_TEST_HOME=other)
        check("a set one is where opencode's home is",
              r["ambient"] == [] and srcs(r["neutralised"]) == [skills], r)
        r = h.scan(OPENCODE_API_KEY="k")
        check("an OpenCode Console key in the environment is refused",
              srcs(r["ambient"]) == ["$OPENCODE_API_KEY"], r["ambient"])


def test_remote_config():
    with host() as h:
        data = os.path.join(h.home, ".local", "share", "opencode")
        put(os.path.join(data, "auth.json"), json.dumps({"openrouter": {"type": "api", "key": "k"},
                                                         "https://corp.example": {"type": "wellknown"}}))
        conn = sqlite3.connect(os.path.join(data, "opencode.db"))
        conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE TABLE credential (id TEXT PRIMARY KEY, integration_id TEXT, value TEXT)")
        conn.execute("INSERT INTO credential VALUES ('c1', 'anthropic', '{}')")
        conn.commit()
        r = h.scan()
        check("a wellknown entry in a 1.x auth.json is refused; provider keys in either store are not",
              [why.split(" ")[0] for _, why in r["ambient"]] == ["`https://corp.example`"], r["ambient"])
        conn.execute("INSERT INTO kv VALUES ('wellknown:sources', '[\"https://corp.example\"]')")
        conn.execute("INSERT INTO kv VALUES ('other', '[1]')")
        conn.execute("INSERT INTO credential VALUES ('c2', 'opencode', '{}')")
        conn.execute("CREATE TABLE account_state (id INTEGER, active_account_id TEXT, active_org_id TEXT)")
        conn.execute("INSERT INTO account_state VALUES (1, 'acc', 'org_1')")
        conn.commit()
        conn.close()
        r = h.scan()
        check("a well-known source, a Console credential and an active console org in the database are refused",
              [why.split(" ")[0] for _, why in r["ambient"]] == ["`https://corp.example`", "well-known", "a", "active"],
              r["ambient"])
        moved = os.path.join(h.d, "moved.db")
        os.rename(os.path.join(data, "opencode.db"), moved)
        check("the database is found where OPENCODE_DB puts it",
              len(h.scan()["ambient"]) == 1 and len(h.scan(OPENCODE_DB=moved)["ambient"]) == 4)


def test_managed_config():
    with host() as h:
        put(os.path.join(h.managed, "opencode.json"), json.dumps({"instructions": ["/x.md"]}))
        plist = os.path.join(h.prefs, pwd.getpwuid(os.getuid()).pw_name, "ai.opencode.managed.plist")
        os.makedirs(os.path.dirname(plist))
        with open(plist, "wb") as f:
            plistlib.dump({"PayloadUUID": "u", "provider": {}, "mcp": {"m": {"type": "local", "command": ["x"]}}}, f)
        r = h.scan()
        check("the managed config and profile are vetted, their metadata ignored",
              [(os.path.basename(s), why.split("`")[1]) for s, why in r["ambient"]] ==
              [("opencode.json", "instructions"), ("ai.opencode.managed.plist", "mcp")], r["ambient"])


def test_launcher_hooks_recorded():
    with host() as h:
        r = h.scan(OPENCODE_CONFIG_CONTENT='{"instructions":["/x.md"]}', OPENCODE_FLAGS="all")
        check("a launcher's OPENCODE_CONFIG_CONTENT and OPENCODE_FLAGS are recorded as blanked",
              r["neutralised"] == [("$OPENCODE_CONFIG_CONTENT", "blanked by RUN_ENV"),
                                   ("$OPENCODE_FLAGS", "blanked by RUN_ENV")] and r["ambient"] == [], r)


def test_check():
    with host() as h, tmpdir(h.tmp):
        ambient.check("argent", h.env)
        check("check() passes a clean host", True)
        p = put(os.path.join(h.config, "AGENTS.md"))
        try:
            ambient.check("none", h.env)
            check("check() refuses an ambient source", False)
        except ambient.AmbientError as e:
            check("check() refuses an ambient source, naming the file and the tool",
                  f"  {p}: loaded into every run's system prompt" in str(e) and "every none run" in str(e), e)
        put(os.path.join(h.tmp, ".opencode", "skills", "s", "SKILL.md"))
        try:
            ambient.check("none", h.env)
            check("check() walks up from where the run dir will be", False)
        except ambient.AmbientError as e:
            check("check() walks up from where the run dir will be",
                  str(e).startswith("2 host source(s)") and os.path.join(h.tmp, ".opencode", "skills") in str(e), e)


def fake_config(tool, udid):
    d = tempfile.mkdtemp(prefix="test-ambient-cfg-")
    rule = put(os.path.join(d, "always-on-rule.md"), "rule")
    put(os.path.join(d, "opencode.json"), json.dumps({"permission": "allow", "instructions": [rule]}))
    return d


def test_run_one_records_and_sets_env():
    with tempfile.TemporaryDirectory() as results, fresh({"none": {"why": "t", "steps": [], "check": None}}), \
            harness(results):
        seen = []

        def fake_opencode(cmd, cwd, timeout, stdout_file, env=None):
            seen.append((cwd, env))
            stdout_file.write(json.dumps({"type": "tool_use", "part": {"tool": "bash"}}) + "\n")
            return 0, "", False

        with patched(bench.gen_configs, write_run_config=fake_config), \
                patched(isolation, run_opencode=fake_opencode):
            bench.run_one("gpt_low", "none", TASK)
        env = json.load(open(os.path.join(results, "gpt_low__none", "bsky-01", "env.json")))
        cwd, run_env = seen[0]
        check("opencode runs under RUN_ENV",
              {k: run_env.get(k) for k in ambient.RUN_ENV} == ambient.RUN_ENV, run_env)
        check("env.json records exactly the run's staged rule as its instructions",
              env["ambient"]["instructions"] == [os.path.join(cwd, "always-on-rule.md")], env.get("ambient"))
        check("... and no ambient source", env["ambient"]["ambient"] == [], env["ambient"])


def test_run_one_ambient_leaves_unit_pending():
    with tempfile.TemporaryDirectory() as results, fresh({"none": {"why": "t", "steps": [], "check": None}}), \
            harness(results):
        put(os.path.join(results, "home", ".config", "opencode", "AGENTS.md"), "MARKER-AGENTS")
        spawned = []
        with patched(isolation, run_opencode=lambda *a, **k: spawned.append(a) or (0, "", False)), \
                patched(sim_device, clone_golden=lambda tid: spawned.append("clone") or "CLONE"):
            try:
                bench.run_one("gpt_low", "none", TASK)
                check("run_one raises on an ambient source", False)
            except ambient.AmbientError as e:
                check("run_one raises on an ambient source", "AGENTS.md" in str(e), e)
        check("no device or agent was touched", spawned == [], spawned)
        check("nothing was written for the unit", not os.path.exists(os.path.join(results, "gpt_low__none")))
        check("the unit stays pending", ledger.needs_run(results, "gpt_low", "none", "bsky-01", bench.HARNESS))


def test_bench_cli_stops_on_ambient():
    calls = []

    def fake_run_one(mk, tool, t, force=False):
        calls.append(t["id"])
        raise ambient.AmbientError("host AGENTS.md")

    tasks = [dict(TASK, id=f"bsky-0{i}") for i in (1, 2)]
    with patched(bench, run_one=fake_run_one, load_tasks=lambda: tasks, check_tool_versions=lambda: None,
                 check_app_versions=lambda: None, ollama_served_tags=lambda: set(),
                 model_runnable=lambda *a: True), \
            patched(isolation, acquire_lock=lambda **k: None), patched(sim_device, check_golden=lambda: {}), \
            patched(sys, argv=["bench.py", "--all", "--models", "gpt_low", "--tools", "none,argent"]):
        try:
            bench.main()
            check("bench.py stops the pass on an ambient source", False)
        except ambient.AmbientError:
            check("bench.py stops the pass on an ambient source", calls == ["bsky-01"], calls)


def test_matrix_stops_on_ambient():
    calls = []

    def fake_run_one(key, tool, t, force=False):
        calls.append((tool, t["id"]))
        raise ambient.AmbientError("host AGENTS.md")

    tasks = [dict(TASK, id=f"bsky-0{i}") for i in (1, 2)]
    with tempfile.TemporaryDirectory() as results, \
            environ(ONLY=bench.EDITION["models"][0], TOOLS="none,argent", APPS="bluesky"), \
            patched(bench, RESULTS=results, run_one=fake_run_one, load_tasks=lambda: tasks,
                    tool_variant=lambda tool: tool), \
            patched(isolation, acquire_lock=lambda **k: None), patched(sim_device, check_golden=lambda: {}):
        try:
            run_matrix.main()
            check("run_matrix stops the pass on an ambient source", False)
        except ambient.AmbientError:
            check("run_matrix stops the pass on an ambient source", calls == [("none", "bsky-01")], calls)


def test_doctor_preflight():
    with host() as h, tmpdir(h.tmp), environ(HOME=h.home, XDG_CONFIG_HOME=None, XDG_DATA_HOME=None):
        check("doctor --preflight passes a clean host", doctor.preflight_ambient(["argent", "none"]) == 0)
        put(os.path.join(h.config, "skills", "s", "SKILL.md"))
        check("doctor --preflight fails each tool on a host skill", doctor.preflight_ambient(["argent", "none"]) == 2)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{PASS} checks passed")
