#!/usr/bin/env python3
"""Ambient opencode input: what opencode loads into a run from the HOST rather than from the run's own
config dir. A run must see what its tool installs - the run dir's opencode.json, its staged skills,
agents and always-on rule - and nothing of the operator's: no personal instructions, MCP servers,
agents, skills or plugins.

Every source opencode 2.3.0 reads for `opencode run --standalone` (packages/core/src/config.ts,
config/discovery.ts, config/plugin/*.ts, plugin/supervisor.ts, wellknown.ts; packages/cli/src/
server-process.ts; packages/util/src/global*.ts), and the harness's answer to each. Checked against the
2.3.0 binary with marker files in every location. <config> is $XDG_CONFIG_HOME/opencode (default
~/.config/opencode), <data> the same under $XDG_DATA_HOME, <sys> is /Library/Application Support/opencode.
opencode walks from the run dir up to `/` for config; a run dir is a temp dir outside any repository.

  Neutralised by RUN_ENV:
    ~/.claude/skills, and .claude/skills up the run dir          OPENCODE_DISABLE_CLAUDE_CODE(_SKILLS)
    OPENCODE_CONFIG, _CONFIG_CONTENT, _CONFIG_DIR, _FLAGS and the other hooks the launcher may export
                                                                 blanked
  Neutralised by the run's own opencode.json (every template sets these):
    every external plugin: a config's `plugins`, any plugin(s)/ dir     plugins: ["-*", "opencode.*", ...]
    ~/.agents/skills and .agents/skills up the run dir (and .claude)    ... "-opencode.config.compatibility"
    a host MCP server the template disables by name, with a full entry (`type`, `command`,
      `enabled: false`); a bare `{"enabled": false}` is ignored by opencode 2
    a host permission rule (`permission: "allow"` is matched last), and a last-document-wins key the
      template sets itself (`websearch`)
  Refused - no switch exists, so check() raises AmbientError naming each one:
    <config>/AGENTS.md; an AGENTS.md between the run dir and its project root (only when an ancestor
      holds .git/.hg, or the run dir is under the home dir)
    agent(s)/, mode(s)/, skill(s)/ in <config>, <sys> or a .opencode/ above the run dir
    any key outside ALLOWED_KEYS in <config>/opencode.json(c), an ancestor's opencode.json(c), an
      ancestor's .opencode/opencode.json(c), <sys>/opencode.json(c), or the ai.opencode.managed profile
    remote config: a `wellknown` source or an OpenCode Console credential in the database (or a
      `wellknown` entry in a 1.x auth.json, which a new database imports), or $OPENCODE_API_KEY
    an empty $OPENCODE_TEST_HOME, which points opencode's home at the run dir
  Let through, as nothing of them reaches the model: slash commands (a config's `commands`, any
  command(s)/*.md), which `opencode run` never executes, and the other keys in ALLOWED_KEYS. Also
  opencode's own built-in skills (`opencode`, `report`) and tools, which are part of the stock agent
  and the same in every cell.
  No longer read by opencode 2: CLAUDE.md and CONTEXT.md anywhere, ~/.opencode, <config>/config.json.
"""
import contextlib, fnmatch, glob, json, os, plistlib, pwd, sqlite3, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(ROOT, "configs")

RUN_ENV = {
    # ~/.claude and every .claude up the run dir (opencode reads them for skills only). The run config's
    # `plugins` drops the loader too; either alone is enough.
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    # Blanked so no host value reaches a run; opencode 2.3.0 treats each of these as unset when empty.
    # OPENCODE_TEST_HOME and OPENCODE_DB are NOT here: it takes an empty one literally (scan() refuses an
    # empty OPENCODE_TEST_HOME and follows a set one, as it does OPENCODE_DB).
    **dict.fromkeys(("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR",
                     "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_CONFIG_PROJECT_DISABLE", "OPENCODE_FLAGS",
                     "OPENCODE_SIMULATE"), ""),
}

# The top-level opencode.json keys a host config may carry into a run, under their 2.x names and the
# 1.x ones opencode still reads. They decide how a request reaches a model (provider routes and
# options, which providers exist; --model overrides `model`, and `small_model` only titles the session)
# or touch nothing the model sees (logging, sharing, updates, the TUI, the serve/web server, undo
# snapshots, the file watcher, slash commands). The bench depends on the first kind: the host's
# provider block is what routes the ollama cells. Every other key reaches the agent - its instructions,
# MCP servers, agents, skills, plugins, permissions, policies and tools, the formatter output fed back
# after an edit, how its context is compacted, the experimental switches - and so does any key a later
# opencode adds, until it is classified here.
ALLOWED_KEYS = {"$schema", "provider", "providers", "model", "small_model", "enabled_providers",
                "disabled_providers", "username", "logLevel", "server", "watcher", "snapshot", "snapshots",
                "share", "autoshare", "autoupdate", "update", "layout", "enterprise", "theme", "keybinds",
                "tui", "command", "commands"}

# Keys opencode takes whole from the LAST document that sets them, so one the run's opencode.json sets
# replaces a host's (packages/core/src/config.ts).
LAST_WINS = {"shell", "default_agent", "tool_output", "websearch", "experimental", "warming", "timezone"}
PERMISSION_KEYS = {"permission", "permissions", "tools"}
PLUGIN_KEYS = {"plugin", "plugins"}
COMPATIBILITY_PLUGIN = "opencode.config.compatibility"   # loads .claude/skills and .agents/skills
HOST_PLUGIN = "\0host"                                   # stands for any plugin that is not opencode's own

MANAGED_DIR = "/Library/Application Support/opencode"
MANAGED_PREFS = "/Library/Managed Preferences"
PLIST_META = {"PayloadDisplayName", "PayloadIdentifier", "PayloadType", "PayloadUUID", "PayloadVersion",
              "_manualProfile"}

FIX = "move it off this host (or out of the way while the bench runs), then re-run"


class AmbientError(RuntimeError):
    """The host would put something of its own into a run that no opencode switch can keep out. Fatal
    for every run on the host: raised before a unit writes anything, so its unit stays pending."""


def _up(start):
    d = start
    while True:
        yield d
        parent = os.path.dirname(d)
        if parent == d:
            return
        d = parent


def _jsonc(text):
    """JSON with // and /* */ comments and trailing commas, the dialect opencode reads its configs in."""
    def strings_kept(text, drop):
        out, i = [], 0
        while i < len(text):
            if text[i] == '"':
                j = i + 1
                while j < len(text) and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                out.append(text[i:j + 1])
                i = j + 1
            else:
                n = drop(text, i)
                if not n:
                    out.append(text[i])
                i += n or 1
        return "".join(out)

    def comment(text, i):
        for opener, closer in (("//", "\n"), ("/*", "*/")):
            if text.startswith(opener, i):
                end = text.find(closer, i + 2)
                return (len(text) if end < 0 else end + len(closer)) - i
        return 0

    text = strings_kept(text, comment)
    return json.loads(strings_kept(text, lambda t, i: t[i] == "," and t[i + 1:].lstrip()[:1] in ("}", "]")))


def _files(root, pattern, nested=True):
    """Files matching `pattern` under `root` (only directly in it unless `nested`), following symlinks,
    dotfiles included."""
    hits = []
    for d, _, names in os.walk(root, followlinks=True):
        if nested or d == root:
            hits += sorted(os.path.join(d, n) for n in names if fnmatch.fnmatch(n, pattern))
    return hits


def _plugin_on(entries, plugin_id):
    """Whether `plugin_id` is still enabled after a config's `plugins` list, which opencode applies in
    order: `-x` disables, `x` enables, `*` is every plugin and `x.*` an id prefix."""
    on = True
    for entry in entries or []:
        if not isinstance(entry, str):
            continue
        pattern = entry[1:] if entry.startswith("-") else entry
        if pattern == "*" or pattern == plugin_id or (pattern.endswith(".*") and plugin_id.startswith(pattern[:-1])):
            on = not entry.startswith("-")
    return on


def _mcp_servers(mcp):
    """{name: entry} for a config's `mcp` value, in the 1.x shape (`mcp.<name>`) or the 2.x one
    (`mcp.servers.<name>`)."""
    if not isinstance(mcp, dict):
        return {}
    servers = {k: v for k, v in mcp.items() if k not in ("servers", "timeout")}
    if isinstance(mcp.get("servers"), dict):
        servers.update(mcp["servers"])
    return servers


def _mcp_off(entry):
    """Whether a run-config MCP entry disables a host server of the same name. opencode 2 replaces the
    host's entry with it only if it is a whole entry; a bare `{"enabled": false}` is dropped."""
    return (isinstance(entry, dict) and entry.get("type") in ("local", "remote")
            and (entry.get("enabled") is False or entry.get("disabled") is True))


def _skill_files(root):
    """The skills opencode finds under a skill(s)/ directory: SKILL.md at any depth, plus top-level *.md."""
    return sorted(set(_files(root, "SKILL.md") + _files(root, "*.md", nested=False)))


def _instruction_dirs(cwd, home):
    """Where opencode looks for AGENTS.md, and globs a relative `instructions` entry: the run dir, then
    up to <home> if the run dir is under it, else up to the project root - the nearest directory with
    .git or .hg, which is the run dir itself when no ancestor has one."""
    if home and (cwd == home or cwd.startswith(home.rstrip(os.sep) + os.sep)):
        stop = home
    else:
        stop = next((d for d in _up(cwd) if any(os.path.exists(os.path.join(d, v)) for v in (".git", ".hg"))), cwd)
    dirs = []
    for d in _up(cwd):
        dirs.append(d)
        if d == stop:
            break
    return dirs


def _instruction_paths(entry, dirs, home):
    """The files one `instructions` entry resolves to, as opencode resolves it."""
    if entry.startswith(("https://", "http://")):
        return [entry]
    path = os.path.join(home, entry[2:]) if entry.startswith("~/") else entry
    if os.path.isabs(path):
        return sorted(glob.glob(path))
    return [p for d in dirs for p in sorted(glob.glob(os.path.join(d, path)))]


def scan(cwd, run_cfg, env=None):
    """What opencode, started under RUN_ENV with `cwd` as its project dir and `run_cfg` as that dir's
    opencode.json, would take from outside the run. Returns
      instructions  the instruction files it loads into the system prompt, in load order
      neutralised   host sources present that RUN_ENV or the run's own config keeps out
      ambient       host sources that would reach the run: [(source, why)]"""
    env = os.environ if env is None else env
    user_home = env.get("HOME") or os.path.expanduser("~")
    home = os.path.realpath(env.get("OPENCODE_TEST_HOME") or user_home)    # opencode's <home>
    config = os.path.join(env.get("XDG_CONFIG_HOME") or os.path.join(user_home, ".config"), "opencode")
    data = os.path.join(env.get("XDG_DATA_HOME") or os.path.join(user_home, ".local", "share"), "opencode")
    cwd = os.path.realpath(cwd)       # opencode walks up from the canonical path (/private/var/... on macOS)
    walk = _instruction_dirs(cwd, home)
    ambient, neutralised, configured = [], [], []

    run_plugins = run_cfg.get("plugins") or run_cfg.get("plugin") or []
    plugins_off = not _plugin_on(run_plugins, HOST_PLUGIN)
    compatibility_off = not _plugin_on(run_plugins, COMPATIBILITY_PLUGIN)
    run_mcp = _mcp_servers(run_cfg.get("mcp"))
    allow_all = (run_cfg.get("permission") or run_cfg.get("permissions")) == "allow"

    if env.get("OPENCODE_TEST_HOME") == "":
        ambient.append(("$OPENCODE_TEST_HOME", "is set but empty, which makes opencode look for ~/.claude and "
                                               "~/.agents in the run dir - unset it"))
    if env.get("OPENCODE_API_KEY"):
        ambient.append(("$OPENCODE_API_KEY", "is an OpenCode Console credential, and Console serves config, "
                                             "instructions and skills into every run - unset it"))

    def config_source(src, cfg, late):
        """One host config document. An early one is loaded before the run's opencode.json, which can
        then replace or outvote some of its keys; a late one is loaded after it and wins."""
        for key, val in cfg.items():
            if key in ALLOWED_KEYS:
                continue
            if key in PLUGIN_KEYS and not late and plugins_off:
                neutralised.append((src, f"{key}: removed by the run's `plugins`"))
            elif key == "mcp" and not late and isinstance(val, dict):
                for name in _mcp_servers(val):
                    if _mcp_off(run_mcp.get(name)):
                        neutralised.append((src, f"mcp.{name}: disabled by the run's config"))
                    else:
                        ambient.append((src, f"MCP server `{name}` joins every run - remove it here, or disable "
                                             f"it in configs/<tool>/opencode.json with a full entry "
                                             f"({{\"type\": \"local\", \"command\": [\"true\"], \"enabled\": false}})"))
                if "timeout" in val:
                    ambient.append((src, "`mcp.timeout` is applied to every run - remove it here"))
            elif key in PERMISSION_KEYS and not late and allow_all:
                neutralised.append((src, f"{key}: outvoted by the run's `permission: \"allow\"`"))
            elif key in LAST_WINS and not late and key in run_cfg:
                neutralised.append((src, f"{key}: replaced by the run's config"))
            else:
                ambient.append((src, f"`{key}` is merged into every run - remove it here (only the "
                                     f"keys in ambient.ALLOWED_KEYS may stay)"))
        for entry in cfg.get("instructions") or []:
            configured.extend(_instruction_paths(entry, walk, home))

    def config_file(path, late):
        if not os.path.isfile(path):
            return
        try:
            cfg = _jsonc(open(path).read())
        except (OSError, ValueError) as e:
            ambient.append((path, f"cannot be read to vet it ({e}) - fix or {FIX}"))
            return
        config_source(path, cfg if isinstance(cfg, dict) else {}, late)

    def config_dir(d):
        """A config directory's definitions: agents, modes, skills and plugins. (Its opencode.json(c) is
        a config document, and its command(s)/ never reach `opencode run`.)"""
        for sub, find in (("agent", lambda p: _files(p, "*.md")), ("agents", lambda p: _files(p, "*.md")),
                          ("mode", lambda p: _files(p, "*.md", nested=False)),
                          ("modes", lambda p: _files(p, "*.md", nested=False)),
                          ("skill", _skill_files), ("skills", _skill_files)):
            found = find(os.path.join(d, sub))
            if found:
                ambient.append((os.path.join(d, sub), f"{len(found)} {sub.rstrip('s')} definition(s) loaded into "
                                                      f"every run - {FIX}"))
        for sub in ("plugin", "plugins"):
            p = os.path.join(d, sub)
            if os.path.isdir(p) and any(n.endswith((".ts", ".js")) or os.path.isdir(os.path.join(p, n))
                                        for n in os.listdir(p)):
                if plugins_off:
                    neutralised.append((p, "plugins: removed by the run's `plugins`"))
                else:
                    ambient.append((p, f"plugins loaded into every run - {FIX}"))

    # Remote config, loaded first and fetched fresh by every run: nothing here can vet it. opencode 2
    # keeps its sources in the database; a new database imports them from a 1.x auth.json.
    try:
        auth = json.load(open(os.path.join(data, "auth.json")))
    except (OSError, ValueError):
        auth = {}
    for url, entry in auth.items() if isinstance(auth, dict) else ():
        if isinstance(entry, dict) and entry.get("type") == "wellknown":
            ambient.append((os.path.join(data, "auth.json"),
                            f"`{url}` serves remote config into every run - `opencode auth logout` it"))
    db = env.get("OPENCODE_DB") or "opencode.db"
    db = db if os.path.isabs(db) else os.path.join(data, db)

    def query(sql):
        try:
            with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                return conn.execute(sql).fetchone()
        except sqlite3.Error:
            return None

    sources = query("SELECT value FROM kv WHERE key = 'wellknown:sources'")
    try:
        sources = json.loads(sources[0]) if sources else None
    except ValueError:
        sources = sources[0]
    if sources:
        ambient.append((db, f"well-known source(s) {json.dumps(sources)} serve remote config into every run - "
                            f"`opencode auth logout` them"))
    if query("SELECT 1 FROM credential WHERE integration_id = 'opencode'"):
        ambient.append((db, "a stored OpenCode Console credential serves config, instructions and skills into "
                            "every run - `opencode auth logout` it"))
    org = query("SELECT active_org_id FROM account_state WHERE active_account_id IS NOT NULL "
                "AND active_org_id IS NOT NULL")
    if org:
        ambient.append((db, f"active console org {org[0]} serves remote config into every run - "
                            f"`opencode account logout`"))

    # The global config, then every opencode.json(c) from `/` down to the run dir (the run's own last).
    for name in ("opencode.json", "opencode.jsonc"):
        config_file(os.path.join(config, name), late=False)
    for d in reversed(list(_up(os.path.dirname(cwd)))):
        for name in ("opencode.json", "opencode.jsonc"):
            config_file(os.path.join(d, name), late=False)
    for entry in run_cfg.get("instructions") or []:
        configured.extend(_instruction_paths(entry, walk, home))

    # Config dirs: <config>, then every .opencode/ above the run dir, whose opencode.json(c) is loaded
    # after the run's. The run's own .opencode/ holds its staged skills and agents.
    config_dir(config)
    for d in _up(os.path.dirname(cwd)):
        d = os.path.join(d, ".opencode")
        if os.path.isdir(d):
            for name in ("opencode.json", "opencode.jsonc"):
                config_file(os.path.join(d, name), late=True)
            config_dir(d)

    # Skills from other agents' directories: ~/.claude and ~/.agents, and the same above the run dir.
    for base in dict.fromkeys([home] + list(_up(os.path.dirname(cwd)))):
        p = os.path.join(base, ".claude", "skills")
        if _skill_files(p):
            neutralised.append((p, "skills: OPENCODE_DISABLE_CLAUDE_CODE"))
        p = os.path.join(base, ".agents", "skills")
        if _skill_files(p):
            if compatibility_off:
                neutralised.append((p, f"skills: the run's `plugins` drops {COMPATIBILITY_PLUGIN}"))
            else:
                ambient.append((p, f"{len(_skill_files(p))} skill definition(s) loaded into every run - {FIX}, or "
                                   f"add \"-{COMPATIBILITY_PLUGIN}\" to `plugins` in configs/<tool>/opencode.json"))

    # Managed config, loaded last: the system directory, a config dir in its own right, and the profile.
    for name in ("opencode.json", "opencode.jsonc"):
        config_file(os.path.join(MANAGED_DIR, name), late=True)
    config_dir(MANAGED_DIR)
    for plist in (os.path.join(MANAGED_PREFS, pwd.getpwuid(os.getuid()).pw_name, "ai.opencode.managed.plist"),
                  os.path.join(MANAGED_PREFS, "ai.opencode.managed.plist")):
        if os.path.isfile(plist):
            try:
                prefs = plistlib.load(open(plist, "rb"))
            except (OSError, plistlib.InvalidFileException) as e:
                ambient.append((plist, f"cannot be read to vet it ({e})"))
            else:
                config_source(plist, {k: v for k, v in prefs.items() if k not in PLIST_META}, late=True)
            break

    # Instruction files, loaded ahead of the configured ones: AGENTS.md from the run dir up to its
    # project root (the run dir itself, for a temp dir outside any repository), then <config>/AGENTS.md.
    files = [os.path.join(d, "AGENTS.md") for d in walk if os.path.isfile(os.path.join(d, "AGENTS.md"))]
    if os.path.isfile(os.path.join(config, "AGENTS.md")):
        files.append(os.path.join(config, "AGENTS.md"))
    ambient += [(p, f"loaded into every run's system prompt - {FIX}") for p in files]
    neutralised += [(f"${k}", "blanked by RUN_ENV") for k, v in RUN_ENV.items() if v == "" and env.get(k)]

    return {"instructions": list(dict.fromkeys(files + configured)), "neutralised": neutralised,
            "ambient": ambient}


def check(tool, env=None):
    """Raise AmbientError if the host would put anything of its own into a run of `tool`. Evaluated for a
    run dir where gen_configs.write_run_config puts one, with the tool's config template as its config."""
    cfg = json.load(open(os.path.join(CONFIGS, tool, "opencode.json")))
    found = scan(os.path.join(tempfile.gettempdir(), f"bench-cfg-{tool}-"), cfg, env)["ambient"]
    if found:
        raise AmbientError(f"{len(found)} host source(s) opencode would load into every {tool} run, which no "
                           f"opencode switch keeps out:\n" + "\n".join(f"  {src}: {why}" for src, why in found))


def record(cfg_dir, env=None):
    """The run's env.json `ambient` entry: scan() of the run dir gen_configs.write_run_config just made.
    Once check() has passed, its instructions are the run's staged ones alone."""
    return scan(cfg_dir, json.load(open(os.path.join(cfg_dir, "opencode.json"))), env)
