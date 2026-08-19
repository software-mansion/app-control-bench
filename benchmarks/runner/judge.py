#!/usr/bin/env python3
"""Scoring pass: Luna xhigh judges each run's final screenshot against the task it was given
(`prompt`) and an exact description of the solved screen (`solved_screen`). Those two must agree —
a solved_screen that demands more than the prompt asked for marks down an agent that obeyed it.
OpenCode is the default judge transport; direct OpenAI API judging remains an explicit override for
models supported by that endpoint. Decoupled from running, resumable. Same judge for every cell = fair.

Usage:
  python3 judge.py                 # score every result lacking a (valid) score.json
  python3 judge.py --rejudge       # re-score everything
  python3 judge.py --model gpt-4o
"""
import os, sys, json, argparse, re, time, base64, urllib.request, shutil, subprocess, socket, tempfile, hashlib
from contextlib import contextmanager
import ledger
import isolation
from bench_platforms import ANDROID, IOS, PLATFORMS
try:
    from bench import HARNESS as CUR_HARNESS, harness_for
except Exception:
    CUR_HARNESS = "isolation-v3-progressive"        # keep in sync with bench.HARNESS
    harness_for = lambda _tool: CUR_HARNESS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(os.path.dirname(ROOT), "data")   # same tree bench.RESULTS writes
JUDGE_MODEL = "openai/gpt-5.6-luna"
JUDGE_VARIANT = "xhigh"  # calibrated on adversarial tab/auth captures; high missed Latest vs Top
JUDGE_BACKEND = "opencode"
JUDGE_CONFIG_DIR = os.path.join(ROOT, "configs", "judge")
JUDGE_PROXY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anthropic-thinking-proxy.js")
try:
    with open(os.path.expanduser("~/.local/share/opencode/auth.json")) as stream:
        _auth = json.load(stream)
except (FileNotFoundError, json.JSONDecodeError):
    _auth = {}
OAI_KEY = os.environ.get("OPENAI_API_KEY") or (_auth.get("openai") or {}).get("key") \
    or (_auth.get("openai") or {}).get("apiKey")
OPENCODE = os.environ.get("BENCH_OPENCODE") or shutil.which("opencode")

def load_tasks(platform="ios"):
    with open(os.path.join(ROOT, "tasks", "tasks.json")) as stream:
        d = json.load(stream)
    tasks = d["tasks"]
    if platform == "android":
        with open(os.path.join(ROOT, "tasks", ANDROID.task_overrides)) as stream:
            android = json.load(stream)
        solved = android["solved_screens"]
        unsupported = set(android.get("evidence", {}).get("unsupported", []))
        tasks = [
            {
                **task,
                "solved_screen": solved.get(task["id"], task.get("solved_screen")),
                "annulled": task["id"] in unsupported,
            }
            for task in tasks
        ]
    return {t["id"]: t for t in tasks}

JUDGE_PROMPT = """You are an impartial grader for a mobile-app agent benchmark. An AI agent was asked to do a task in an app shown on {device_context}. Judge ONLY whether the agent accomplished the task it was given, based on the attached FINAL screenshot plus the list of device actions the agent took.

APP: {app}
TASK GIVEN TO AGENT: {prompt}
EXACT DESCRIPTION OF THE SOLVED SCREEN (what the final screenshot looks like once the task has been carried out correctly): {solved_screen}
DEVICE ACTIONS THE AGENT TOOK (in order): {tools}
NUMBER OF ACTIONS: {n}
DETERMINISTIC SERVER POSTCONDITION: {postcondition}

The two descriptions above are the whole criterion, and they describe one and the same outcome:
- TASK GIVEN TO AGENT is what the agent was actually asked to do. It defines the goal, and nothing beyond it is required of the agent.
- EXACT DESCRIPTION OF THE SOLVED SCREEN is the visual specification of that same goal: it is what the screen looks like once that task, and only that task, has been done. It is what you match the FINAL screenshot against.
- A passing bluesky-postcondition/v1 from atproto-repo is authoritative proof that the exact requested published mutation exists; it cannot be contradicted by a stale or visually ambiguous screenshot.
- Use them together. If they ever appear to disagree, the TASK GIVEN TO AGENT is the authority on what was asked: never mark an agent down for not doing something its task never asked for.

Carefully read the on-screen text and compare the FINAL screenshot against the EXACT DESCRIPTION OF THE SOLVED SCREEN above. When a task requires the agent to type or post specific text, treat automatic first-letter capitalization by the mobile keyboard and differing/added trailing punctuation as an acceptable match (they are keyboard artifacts, not agent errors) — only extra, missing, or changed WORDS count as a text mismatch. Grade as:
- "success": the final screenshot shows the solved screen and the task given to the agent is plainly accomplished (or, for actions whose result isn't fully visible like scrolling, the actions taken plainly accomplish it).
- "partial": meaningful progress toward the solved screen but it is not fully reached (e.g. the right screen but the specific element/state described is missing).
- "fail": the solved screen is not reached and the task is not accomplished (wrong screen, no progress, stuck, or it never acted).

Reply with ONLY a single JSON object on one line, no prose, no code fences:
{{"verdict":"success|partial|fail","confidence":0.0-1.0,"reason":"<one concise sentence>"}}"""

def parse_opencode_text(stdout):
    texts = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts)


def valid_judge_score(score, model=JUDGE_MODEL, variant=JUDGE_VARIANT):
    if not isinstance(score, dict) or score.get("verdict") not in ("success", "partial", "fail"):
        return False
    if score.get("judge_model") == model and score.get("judge_variant") == variant:
        return True
    postcondition = ((score.get("judge_input") or {}).get("postcondition") or {})
    return (
        score.get("judge_model") == "deterministic:atproto-repo"
        and score.get("judge_variant") is None
        and postcondition.get("schema") == "bluesky-postcondition/v1"
        and postcondition.get("source") == "atproto-repo"
        and postcondition.get("passed") is True
        and postcondition.get("task") == score.get("task")
    )


def _free_loopback_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


@contextmanager
def _judge_proxy(api_key):
    """Run a judge-only credential proxy; the OpenCode child receives a placeholder key."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for the OpenCode judge proxy")
    port = _free_loopback_port()
    proxy_env = {
        key: os.environ[key]
        for key in ("PATH", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
                    "NODE_EXTRA_CA_CERTS")
        if os.environ.get(key)
    }
    proxy_env.update({
        "BENCH_PROXY_PROVIDER": "openai",
        "BENCH_EFFORT_JSON": "{}",
        "OPENAI_API_KEY": api_key,
    })
    with tempfile.TemporaryFile(mode="w+") as proxy_log:
        proxy = subprocess.Popen(
            [node, JUDGE_PROXY, str(port)],
            stdout=proxy_log,
            stderr=subprocess.STDOUT,
            env=proxy_env,
            start_new_session=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline and proxy.poll() is None and not isolation._port_up(port):
            time.sleep(0.05)
        if proxy.poll() is not None or not isolation._port_up(port):
            proxy_log.seek(0)
            detail = proxy_log.read()[-500:].strip()
            raise RuntimeError(f"judge credential proxy failed to start: {detail or 'no diagnostics'}")
        try:
            yield f"http://127.0.0.1:{port}/v1"
        finally:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=5)


def _write_judge_config(directory, base_url):
    template_path = os.path.join(JUDGE_CONFIG_DIR, "opencode.json")
    with open(template_path) as stream:
        config = json.load(stream)
    config.setdefault("provider", {}).setdefault("openai", {}).setdefault("options", {})[
        "baseURL"
    ] = base_url
    config_path = os.path.join(directory, "opencode.json")
    with open(config_path, "w") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")
    return config_path


def call_vision_via_opencode(prompt, png_path, model, variant):
    if not OPENCODE:
        raise RuntimeError("OpenCode executable not found")
    if not OAI_KEY:
        raise RuntimeError("OpenCode judge proxy needs an OpenAI API key")
    routed_model = model if "/" in model else f"openai/{model}"
    with _judge_proxy(OAI_KEY) as base_url, tempfile.TemporaryDirectory(
        prefix="bench-judge-config-"
    ) as config_dir:
        config_path = _write_judge_config(config_dir, base_url)
        child_env = isolation.prepare_opencode_env(config_dir, {
            "OPENCODE_CONFIG": config_path,
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
        })
        result = subprocess.run(
            [OPENCODE, "run", "--pure", "--format", "json", "--model", routed_model,
             "--variant", variant,
             prompt, "--file", png_path],
            cwd=JUDGE_CONFIG_DIR,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[-500:]
        raise RuntimeError(
            f"OpenCode judge failed with exit {result.returncode}: {detail or 'no diagnostics'}"
        )
    text = parse_opencode_text(result.stdout)
    if not text.strip():
        raise RuntimeError("OpenCode judge returned an empty response")
    return text


def call_vision(prompt, png_path, model, variant=JUDGE_VARIANT, retries=5, backend=JUDGE_BACKEND):
    # Provider-qualified model IDs are OpenCode routes, not OpenAI API model names. Keep `auto`
    # safe for callers that explicitly request it even when this host also has a direct API key.
    if backend == "opencode" or (backend == "auto" and ("/" in model or not OAI_KEY)):
        return call_vision_via_opencode(prompt, png_path, model, variant)
    with open(png_path, "rb") as stream:
        b64 = base64.b64encode(stream.read()).decode()
    # The direct chat-completions path uses max_completion_tokens. Headroom is generous so hidden
    # reasoning tokens do not starve the short JSON verdict.
    body = json.dumps({"model": model, "max_completion_tokens": 2000, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                headers={"Authorization": f"Bearer {OAI_KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as response:
                r = json.load(response)
            txt = (r.get("choices", [{}])[0].get("message", {}) or {}).get("content")
            if txt and txt.strip():
                return txt
        except Exception as e:
            wait = min(2 ** attempt, 20)
            time.sleep(wait)
    raise RuntimeError("direct OpenAI judge returned an empty response after retries")

def judge_one(result_dir, task, model, variant=JUDGE_VARIANT, rejudge=False,
              results_root=RESULTS, backend=JUDGE_BACKEND, expected_harness=None,
              device_context="an iOS Simulator; the screenshot may include Safari browser chrome"):
    score_path = os.path.join(result_dir, "score.json")
    if os.path.exists(score_path) and not rejudge:
        try:
            with open(score_path) as stream:
                prev = json.load(stream)
            if valid_judge_score(prev, model, variant):
                # Keep valid scores (only errors are re-done unless --rejudge), but still mark: a judge
                # that resumes over already-scored runs would otherwise leave them `completed` in the
                # persisted ledger forever. mark() is monotonic and disk-derived, so this is idempotent.
                meta = ledger._read_json(os.path.join(result_dir, "meta.json")) or {}
                if meta.get("model"):
                    ledger.mark(results_root, meta["model"], meta["tool"], meta["task"])
                return prev
        except Exception:
            pass
    meta_path = os.path.join(result_dir, "meta.json")
    png = os.path.join(result_dir, "final.png")
    if not (os.path.exists(meta_path) and os.path.exists(png)):
        return None
    with open(meta_path) as stream:
        meta = json.load(stream)
    # Skip stale/polluted runs (older harness): they don't count and are slated for rerun — no API spend.
    expected_harness = expected_harness or harness_for(meta.get("tool"))
    if (meta.get("versions") or {}).get("harness") != expected_harness:
        return None
    solved = task.get("solved_screen") or "(not provided — judge from the task given to the agent and the app's expected end state)"
    # Exactly what the judge is shown; persisted into score.json so any verdict can be audited later.
    judge_input = {
        "app": task["app"],
        "prompt": task["prompt"],
        "solved_screen": solved,
        "tool_names": list(meta.get("tool_names") or []),
        "n_tool_calls": meta.get("n_tool_calls", 0),
        "postcondition": meta.get("postcondition"),
    }
    prompt = JUDGE_PROMPT.format(device_context=device_context,
                                 app=judge_input["app"], prompt=judge_input["prompt"],
                                 solved_screen=judge_input["solved_screen"],
                                 tools=", ".join(judge_input["tool_names"]) or "(none)",
                                 n=judge_input["n_tool_calls"],
                                 postcondition=json.dumps(judge_input["postcondition"], sort_keys=True))
    postcondition = judge_input["postcondition"] or {}
    deterministic = (
        postcondition.get("schema") == "bluesky-postcondition/v1"
        and postcondition.get("source") == "atproto-repo"
        and postcondition.get("task") == meta.get("task")
        and postcondition.get("passed") is True
    )
    if deterministic:
        verdict = {"verdict": "success", "confidence": 1.0,
                   "reason": "Authoritative ATProto records contain the exact requested published mutation."}
        judge_model = "deterministic:atproto-repo"
        judge_variant = None
    else:
        text = call_vision(prompt, png, model, variant=variant, backend=backend)
        m = re.search(r'\{[^{}]*"verdict"\s*:\s*"(success|partial|fail)"[^{}]*\}', text)
        if m:
            try:
                verdict = json.loads(m.group(0))
            except Exception:
                verdict = {"verdict": m.group(1), "confidence": None, "reason": "parse-fallback"}
        else:
            verdict = {"verdict": "error", "confidence": None, "reason": "no verdict parsed", "raw": text[:200]}
        judge_model = model
        judge_variant = variant
    verdict["judge_model"] = judge_model
    verdict["judge_variant"] = judge_variant
    verdict["cell"] = meta["cell"]; verdict["task"] = meta["task"]
    verdict["judge_input"] = judge_input      # what the judge was given (see docs/judging.md)
    verdict["judge_prompt"] = prompt          # the full rendered prompt, verbatim
    # A judge-side failure (API flake, 5 retries exhausted) must never destroy a verdict we already
    # had: on --rejudge, keep the old one rather than downgrading a real grade to "error".
    if verdict["verdict"] == "error" and os.path.exists(score_path):
        try:
            with open(score_path) as stream:
                prev = json.load(stream)
            if prev.get("verdict") in ("success", "partial", "fail"):
                print(f"  {meta['cell']}/{meta['task']}: judge FAILED — keeping prior {prev['verdict']}", flush=True)
                return prev
        except Exception:
            pass
    # Atomic: a half-written score.json is unparseable, and report.py's collect() would die on it.
    tmp = score_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(verdict, fh, indent=2)
    os.replace(tmp, score_path)
    if verdict.get("verdict") in ("success", "partial", "fail"):
        ledger.mark(results_root, meta["model"], meta["tool"], meta["task"])   # completed -> judged (monotonic)
    print(f"  {meta['cell']}/{meta['task']}: {verdict['verdict']}  ({str(verdict.get('reason',''))[:60]})", flush=True)
    time.sleep(0.4)
    return verdict


def find_identical_screenshot_conflicts(results, cells, task_ids):
    """Find same-task, same-image verdict conflicts without treating different judge inputs as equal."""
    conflicts = []
    for task_id in sorted(task_ids):
        groups = {}
        for cell in sorted(cells):
            result_dir = os.path.join(results, cell, task_id)
            try:
                with open(os.path.join(result_dir, "final.png"), "rb") as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
                with open(os.path.join(result_dir, "score.json")) as stream:
                    score = json.load(stream)
            except (OSError, json.JSONDecodeError):
                continue
            verdict = score.get("verdict")
            if verdict in ("success", "partial", "fail"):
                groups.setdefault(digest, []).append((cell, verdict, score.get("judge_input")))
        for records in groups.values():
            if len(records) < 2 or len({verdict for _, verdict, _ in records}) < 2:
                continue
            judge_inputs = [judge_input for _, _, judge_input in records]
            conflicts.append({
                "task": task_id,
                "cells": [cell for cell, _, _ in records],
                "verdicts": [verdict for _, verdict, _ in records],
                "same_judge_input": (
                    all(value is not None for value in judge_inputs)
                    and len({json.dumps(value, sort_keys=True) for value in judge_inputs}) == 1
                ),
            })
    return conflicts

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rejudge", action="store_true")
    ap.add_argument("--model", default=JUDGE_MODEL)
    ap.add_argument("--variant", default=JUDGE_VARIANT)
    ap.add_argument("--app", default=None, help="only judge tasks for this app (e.g. bluesky, element)")
    ap.add_argument("--task", default=None, help="comma-separated task ids to (re)judge (e.g. bsky-13,bsky-16)")
    ap.add_argument("--cell", default=None, help="comma-separated result cell dirs to judge (e.g. gpt_low__none)")
    ap.add_argument("--platform", choices=tuple(PLATFORMS), default=IOS.id)
    ap.add_argument("--results", help="result tree to judge (defaults to platform data tree)")
    ap.add_argument("--judge-backend", choices=("auto", "direct", "opencode"), default=JUDGE_BACKEND)
    a = ap.parse_args()
    assert (a.judge_backend != "direct" or OAI_KEY), "direct judge needs an OpenAI API key"
    assert (a.judge_backend != "opencode" or OPENCODE), "OpenCode judge needs an OpenCode executable"
    assert OAI_KEY or OPENCODE, "no OpenAI API key or authenticated OpenCode executable"
    platform = PLATFORMS[a.platform]
    results = a.results or os.path.join(os.path.dirname(ROOT), platform.results_dir)
    tasks = load_tasks(a.platform)
    only_tasks = set(a.task.split(",")) if a.task else None
    only_cells = set(a.cell.split(",")) if a.cell else None
    n = 0
    selected_scores = []
    seen_cells = set()
    for cell in sorted(os.listdir(results)) if os.path.isdir(results) else []:
        cdir = os.path.join(results, cell)
        if not os.path.isdir(cdir):
            continue
        if only_cells and cell not in only_cells:
            continue
        seen_cells.add(cell)
        for tid in sorted(os.listdir(cdir)):
            rdir = os.path.join(cdir, tid)
            if not os.path.isdir(rdir) or tid not in tasks:
                continue
            if tasks[tid].get("annulled"):          # annulled task — impossible to judge fairly; not scored
                continue
            if a.app and tasks[tid].get("app") != a.app:
                continue
            if only_tasks and tid not in only_tasks:
                continue
            score = judge_one(
                rdir, tasks[tid], a.model, a.variant, a.rejudge, results, a.judge_backend,
                platform.published_harness("agent-device") if a.platform == "android" else None,
                f"an {platform.device}" if a.platform == "android" else
                "an iOS Simulator; the screenshot may include Safari browser chrome",
            )
            selected_scores.append((cell, tid, score))
            if score:
                n += 1
    print(f"== judged {n} results ==")
    if only_cells:
        invalid = [
            f"{cell}/{tid}" for cell, tid, score in selected_scores
            if not valid_judge_score(score)
        ]
        invalid.extend(f"{cell}/<missing cell>" for cell in sorted(only_cells - seen_cells))
        if invalid:
            print(
                "judge validation FAILED: selected results must be Luna xhigh or deterministic: "
                + ", ".join(invalid),
                file=sys.stderr,
            )
            raise SystemExit(1)

    if a.platform == "android":
        conflicts = find_identical_screenshot_conflicts(
            results, only_cells or seen_cells, only_tasks or set(tasks)
        )
        if conflicts:
            print(json.dumps({"identical_screenshot_verdict_conflicts": conflicts}), file=sys.stderr)
            if any(conflict["same_judge_input"] for conflict in conflicts):
                raise SystemExit(2)

if __name__ == "__main__":
    main()
