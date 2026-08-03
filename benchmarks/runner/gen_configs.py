#!/usr/bin/env python3
"""Generate opencode configs with the machine-specific fields resolved from bench_env — so a new
machine (or a new sim udid) needs ZERO hand-editing, and argent + agent-device can never drift to
different udids. Only the machine-specific fields are rewritten; everything else in each config
(proxy baseURLs, permission, disabled servers) is preserved.

The tracked configs/<tool>/opencode.json is a TEMPLATE: write_run_config(tool, udid) copies it
with the run's clone udid baked in to a temp dir, which is used as opencode's cwd and deleted
with the run. Nothing shared, nothing stale — the udid in the tracked file is never trusted.
"""
import os, sys, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(ROOT, "configs")


def _load(p):
    return json.load(open(p))


def _dump(p, d):
    with open(p, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")


# Where each tool's REAL shipped skills/rule/agents live, so the benchmark stages exactly what
# `argent init` / `npx skills add callstack/agent-device` would install — nothing hand-assembled.
#   skills_dir  -> copied verbatim into <run>/.opencode/skills/ (opencode's native, progressive
#                  disclosure surface: the agent sees a name+description index and loads a SKILL.md
#                  body on demand via the `skill` tool — exactly the real experience).
#   rule_file   -> the tool's alwaysApply rule, staged (frontmatter stripped) as opencode `instructions`
#                  (opencode has no `.opencode/rules`; instructions is its always-on channel). None if
#                  the tool ships no always-on rule (agent-device routes via its SKILL.md instead).
#   agents_dir  -> the tool's subagent defs, copied into <run>/.opencode/agents/ (part of a faithful
#                  install). None if the tool ships none.
# bench.py sets OPENCODE_DISABLE_EXTERNAL_SKILLS=1 + OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 for the run,
# so the agent sees ONLY these staged skills — never the machine's ambient ~/.agents / ~/.claude skills.
def _skill_sources(tool):
    if tool == "argent":
        return (bench_env.argent_skills_dir(), bench_env.argent_rule_file(), bench_env.argent_agents_dir())
    if tool == "agent-device":
        return (bench_env.agent_device_skills_dir(), None, None)
    return (None, None, None)


def _strip_frontmatter(text):
    """Drop a leading `--- ... ---` YAML block (skill/rule metadata) — the always-on rule body is what
    a real agent has in context; the frontmatter is install metadata, not guidance."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1:] if nl != -1 else ""
    return text


def _stage_skills(tool, dest_dir):
    """Copy the tool's shipped skill dirs (incl. their references/) into <dest_dir>/.opencode/skills/,
    stage any always-on rule as instructions, and copy any subagent defs. Returns the instructions list
    (absolute paths) for the always-on rule, or [] if none."""
    skills_dir, rule_file, agents_dir = _skill_sources(tool)
    instructions = []
    if skills_dir and os.path.isdir(skills_dir):
        skdst = os.path.join(dest_dir, ".opencode", "skills")
        os.makedirs(skdst, exist_ok=True)
        for name in sorted(os.listdir(skills_dir)):
            src = os.path.join(skills_dir, name)
            if os.path.isdir(src) and os.path.exists(os.path.join(src, "SKILL.md")):
                shutil.copytree(src, os.path.join(skdst, name))
    if rule_file and os.path.exists(rule_file):
        rdst = os.path.join(dest_dir, "always-on-rule.md")
        with open(rdst, "w") as f:
            f.write(_strip_frontmatter(open(rule_file).read()))
        instructions.append(rdst)
    if agents_dir and os.path.isdir(agents_dir):
        agdst = os.path.join(dest_dir, ".opencode", "agents")
        shutil.copytree(agents_dir, agdst)
    return instructions


def desired(udid):
    """The machine-specific MCP command array argent should hold for this machine + udid. (agent-device
    is CLI-first — it has no MCP server in the benchmark; the agent drives it through a shell.)"""
    return {
        "argent": [bench_env.argent(), "mcp"],
    }


def write_run_config(tool, udid, configs_root=None):
    """Per-run opencode config dir for `tool`, pointed at the run's clone `udid`. Copies the tracked
    template (configs/<tool>/opencode.json) and stages the tool's REAL shipped skills the way a normal
    install would, so the agent sees exactly what it would in the wild:
      - skills go to <run>/.opencode/skills/ (opencode's native, progressively-disclosed surface — an
        index the agent loads bodies from on demand via the `skill` tool). bench.py sets the
        DISABLE_EXTERNAL/CLAUDE_CODE_SKILLS env so ONLY these staged skills are visible (no ambient
        machine skills leak in).
      - any always-on rule (argent's alwaysApply rule) is staged as `instructions` (opencode's always-on
        channel); agent-device ships no rule (its SKILL.md router carries the guidance instead).
      - argent (MCP-first): points mcp.argent.command at the `argent mcp` server.
      - agent-device (CLI-first): drops any MCP server and writes agent-device-cli.json (the clone as
        the CLI default target); bench.py exports AGENT_DEVICE_CONFIG + the shim dir so the agent's
        bare `agent-device ...` shell commands hit this clone.
      - none (baseline): stages NO skills and NO device MCP/CLI target — the agent gets only opencode's
        stock shell + this machine's Xcode CLI (xcrun simctl), the way a plain coding-agent harness
        would. The run's clone udid is named in the task preamble so bare simctl commands can target it.
    Returns the temp dir (use as opencode's cwd; caller deletes it with the run)."""
    root = configs_root or CONFIGS
    cfg = _load(os.path.join(root, tool, "opencode.json"))
    d = tempfile.mkdtemp(prefix=f"bench-cfg-{tool}-")
    cfg["instructions"] = _stage_skills(tool, d)       # real skills -> .opencode/skills/; rule -> instructions
    perm = cfg.get("permission")                       # ensure the harness `skill` tool is auto-approved
    if isinstance(perm, dict):
        perm.setdefault("skill", "allow")
    elif perm != "allow":
        cfg["permission"] = "allow"
    if tool == "agent-device":
        cfg.get("mcp", {}).pop("agent-device", None)   # CLI-first: no MCP server for this tool
        _dump(os.path.join(d, "agent-device-cli.json"),
              {"udid": udid, "platform": "ios", "iosSimulatorDeviceSet": bench_env.device_set()})
    elif tool == "argent":
        cfg["mcp"][tool]["command"] = desired(udid)[tool]
    # tool == "none": the baseline — no device MCP, no CLI target, no skills staged. The agent gets
    # only opencode's stock shell + this machine's Xcode CLI (the run's clone udid is named in the
    # task preamble, so bare `xcrun simctl` commands can target it).
    _dump(os.path.join(d, "opencode.json"), cfg)
    return d


if __name__ == "__main__":
    # debug aid: show what a run config would hold for a given udid, without writing anything
    udid = (sys.argv[1:] or [bench_env.udid() or "<udid>"])[0]
    print(json.dumps(desired(udid), indent=2))
