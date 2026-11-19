#!/usr/bin/env python3
"""Scoring pass: a vision model judges each run's final screenshot against the task it was given (`prompt`)
AND an exact description of the solved screen (`solved_screen`). Those two must agree — a solved_screen that
demands more than the prompt asked for marks down an agent that obeyed it. Calls the OpenAI API DIRECTLY with GPT-5.4 (vision) at
temperature 0 — deterministic + strong OCR (gpt-4.1-mini mis-read text and produced false pos/neg).
Decoupled from running, resumable. Same judge for every cell = fair.

Usage:
  python3 judge.py                 # score every result lacking a (valid) score.json
  python3 judge.py --rejudge       # re-score everything
  python3 judge.py --model gpt-4o
  python3 judge.py --stale         # score older-harness runs instead, each against its day's tasks.json
"""
import os, json, argparse, re, time, base64, subprocess, urllib.request, urllib.error
import ledger, bench_env, editions
try:
    from bench import HARNESS as CUR_HARNESS       # current faithful harness stamp
except Exception:
    CUR_HARNESS = "isolation-v5"                    # keep in sync with bench.HARNESS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The judge and the results it scores are the current edition's (configs/editions.json): verdicts from
# two judges are not comparable, so a judge change starts a new edition rather than rescoring an old one.
EDITION = editions.current()
RESULTS = editions.results_dir(EDITION)
JUDGE_MODEL = EDITION.get("judge") or "gpt-6.1-sol"   # vision-capable, strong OCR
OAI_KEY = bench_env.auth_key("openai")    # the key the runs use, from OpenCode's credential store

def load_tasks():
    d = json.load(open(os.path.join(ROOT, "tasks", "tasks.json")))
    return {t["id"]: t for t in d["tasks"]}

def tasks_at(ts, _cache={}):
    """(rev, {id: task}) for tasks.json as last committed before unix time `ts`. A stale run was given
    that day's prompt, which can differ from today's (bsky-03 asked for "photography" until 2026-07-09)."""
    rev = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%H", f"--before=@{ts}", "--",
                          "tasks/tasks.json"], capture_output=True, text=True, check=True).stdout.strip()
    if not rev:
        raise SystemExit(f"no tasks/tasks.json committed before {ts} - a shallow clone?")
    if rev not in _cache:
        raw = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:tasks/tasks.json"],
                             capture_output=True, text=True, check=True).stdout
        _cache[rev] = {t["id"]: t for t in json.loads(raw)["tasks"]}
    return rev, _cache[rev]

JUDGE_PROMPT = """You are an impartial grader for a mobile-app agent benchmark. An AI agent was asked to do a task in an app shown inside an iOS Simulator (the screenshot may include the Safari browser chrome around a web app). Judge ONLY whether the agent accomplished the task it was given, based on the attached FINAL screenshot plus the list of device actions the agent took.

APP: {app}
TASK GIVEN TO AGENT: {prompt}
EXACT DESCRIPTION OF THE SOLVED SCREEN (what the final screenshot looks like once the task has been carried out correctly): {solved_screen}
DEVICE ACTIONS THE AGENT TOOK (in order): {tools}
NUMBER OF ACTIONS: {n}

The two descriptions above are the whole criterion, and they describe one and the same outcome:
- TASK GIVEN TO AGENT is what the agent was actually asked to do. It defines the goal, and nothing beyond it is required of the agent.
- EXACT DESCRIPTION OF THE SOLVED SCREEN is the visual specification of that same goal: it is what the screen looks like once that task, and only that task, has been done. It is what you match the FINAL screenshot against.
- Use them together. If they ever appear to disagree, the TASK GIVEN TO AGENT is the authority on what was asked: never mark an agent down for not doing something its task never asked for.

Carefully read the on-screen text and compare the FINAL screenshot against the EXACT DESCRIPTION OF THE SOLVED SCREEN above. When a task requires the agent to type or post specific text, treat automatic first-letter capitalization by the mobile keyboard and differing/added trailing punctuation as an acceptable match (they are keyboard artifacts, not agent errors) — only extra, missing, or changed WORDS count as a text mismatch. Grade as:
- "success": the final screenshot shows the solved screen and the task given to the agent is plainly accomplished (or, for actions whose result isn't fully visible like scrolling, the actions taken plainly accomplish it).
- "partial": meaningful progress toward the solved screen but it is not fully reached (e.g. the right screen but the specific element/state described is missing).
- "fail": the solved screen is not reached and the task is not accomplished (wrong screen, no progress, stuck, or it never acted).

Reply with ONLY a single JSON object on one line, no prose, no code fences:
{{"verdict":"success|partial|fail","confidence":0.0-1.0,"reason":"<one concise sentence>"}}"""

_NO_TEMPERATURE = set()     # models that rejected `temperature`, learned from their own 400


def vision_body(prompt, b64, model):
    """The chat/completions request for one verdict. max_completion_tokens, not max_tokens (which
    400s); headroom is generous so hidden reasoning tokens don't starve the short JSON verdict.
    temperature 0 is sent to every model that takes it; some newer ones accept only the default."""
    body = {"model": model, "max_completion_tokens": 2000,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
    if model not in _NO_TEMPERATURE:
        body["temperature"] = 0
    return body


def call_vision(prompt, png_path, model, retries=5, post=None):
    b64 = base64.b64encode(open(png_path, "rb").read()).decode()
    post = post or _post_openai
    attempt = 0
    while attempt < retries:
        try:
            r = post(vision_body(prompt, b64, model))
            txt = (r.get("choices", [{}])[0].get("message", {}) or {}).get("content")
            if txt and txt.strip():
                return txt
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf8", "replace") if e.fp else ""
            if e.code == 400 and "temperature" in detail and model not in _NO_TEMPERATURE:
                _NO_TEMPERATURE.add(model)      # resend at once without it; not a failed attempt
                continue
            print(f"  judge: {model} answered HTTP {e.code}: {detail[:300]}", flush=True)
            time.sleep(min(2 ** attempt, 20))
        except Exception:
            time.sleep(min(2 ** attempt, 20))
        attempt += 1
    return ""


def _post_openai(body):
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {OAI_KEY}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=90))

def judge_one(result_dir, task, model, rejudge=False, tasks_rev=None):
    """Judge one current-harness run, or with `tasks_rev` (the tasks.json `task` came from), one stale run."""
    score_path = os.path.join(result_dir, "score.json")
    if os.path.exists(score_path) and not rejudge:
        try:
            prev = json.load(open(score_path))
            if prev.get("verdict") in ("success", "partial", "fail"):
                # Keep valid scores (only errors are re-done unless --rejudge), but still mark: a judge
                # that resumes over already-scored runs would otherwise leave them `completed` in the
                # persisted ledger forever. mark() is monotonic and disk-derived, so this is idempotent.
                meta = ledger._read_json(os.path.join(result_dir, "meta.json")) or {}
                if meta.get("model"):
                    ledger.mark(RESULTS, meta["model"], ledger.run_variant(meta), meta["task"])
                return prev
        except Exception:
            pass
    meta_path = os.path.join(result_dir, "meta.json")
    png = os.path.join(result_dir, "final.png")
    if not (os.path.exists(meta_path) and os.path.exists(png)):
        return None
    meta = json.load(open(meta_path))
    # Stale runs are slated for rerun, so they are judged only on request (--stale), never by default.
    if ((meta.get("versions") or {}).get("harness") != CUR_HARNESS) != (tasks_rev is not None):
        return None
    # `success` is the pre-2026-07-10 name of the criterion, still carried by tasks_at()'s older tasks.
    solved = task.get("solved_screen") or task.get("success") or "(not provided — judge from the task given to the agent and the app's expected end state)"
    # Exactly what the judge is shown; persisted into score.json so any verdict can be audited later.
    judge_input = {
        "app": task["app"],
        "prompt": task["prompt"],
        "solved_screen": solved,
        "tool_names": list(meta.get("tool_names") or []),
        "n_tool_calls": meta.get("n_tool_calls", 0),
    }
    if tasks_rev:
        judge_input["tasks_rev"] = tasks_rev
    prompt = JUDGE_PROMPT.format(app=judge_input["app"], prompt=judge_input["prompt"],
                                 solved_screen=judge_input["solved_screen"],
                                 tools=", ".join(judge_input["tool_names"]) or "(none)",
                                 n=judge_input["n_tool_calls"])
    text = call_vision(prompt, png, model)
    m = re.search(r'\{[^{}]*"verdict"\s*:\s*"(success|partial|fail)"[^{}]*\}', text)
    if m:
        try:
            verdict = json.loads(m.group(0))
        except Exception:
            verdict = {"verdict": m.group(1), "confidence": None, "reason": "parse-fallback"}
    else:
        verdict = {"verdict": "error", "confidence": None, "reason": "no verdict parsed", "raw": text[:200]}
    verdict["judge_model"] = model
    verdict["cell"] = meta["cell"]; verdict["task"] = meta["task"]
    verdict["judge_input"] = judge_input      # what the judge was given (see docs/judging.md)
    verdict["judge_prompt"] = prompt          # the full rendered prompt, verbatim
    # A judge-side failure (API flake, 5 retries exhausted) must never destroy a verdict we already
    # had: on --rejudge, keep the old one rather than downgrading a real grade to "error".
    if verdict["verdict"] == "error" and os.path.exists(score_path):
        try:
            prev = json.load(open(score_path))
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
        ledger.mark(RESULTS, meta["model"], ledger.run_variant(meta), meta["task"])  # -> judged (monotonic)
    print(f"  {meta['cell']}/{meta['task']}: {verdict['verdict']}  ({str(verdict.get('reason',''))[:60]})", flush=True)
    time.sleep(0.4)
    return verdict

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rejudge", action="store_true")
    ap.add_argument("--model", default=None, help="judge model (default: the edition's `judge`)")
    ap.add_argument("--edition", default=EDITION["id"],
                    help="judge this edition's results, with its harness and its judge (default: the "
                         "current one). A past edition's runs keep the judge they were scored with.")
    ap.add_argument("--app", default=None, help="only judge tasks for this app (e.g. bluesky, element)")
    ap.add_argument("--task", default=None, help="comma-separated task ids to (re)judge (e.g. bsky-13,bsky-16)")
    ap.add_argument("--cell", default=None, help="comma-separated result cell dirs to judge (e.g. gpt_low__none)")
    ap.add_argument("--stale", action="store_true",
                    help="judge only older-harness runs, each against tasks.json as of its run")
    a = ap.parse_args()
    global RESULTS, CUR_HARNESS
    edition = editions.get(a.edition)
    if edition["id"] != EDITION["id"]:
        RESULTS, CUR_HARNESS = editions.results_dir(edition), edition.get("harness")
    a.model = a.model or edition.get("judge") or JUDGE_MODEL
    print(f"judge: edition {edition['id']} ({RESULTS}), model {a.model}", flush=True)
    assert OAI_KEY, "no OpenAI key in OpenCode's credential store (opencode auth list)"
    tasks = load_tasks()
    only_tasks = set(a.task.split(",")) if a.task else None
    only_cells = set(a.cell.split(",")) if a.cell else None
    n = 0
    for cell in sorted(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else []:
        cdir = os.path.join(RESULTS, cell)
        if not os.path.isdir(cdir):
            continue
        if only_cells and cell not in only_cells:
            continue
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
            task, rev = tasks[tid], None
            if a.stale:
                mp = os.path.join(rdir, "meta.json")
                if not os.path.exists(mp):
                    continue
                rev, era = tasks_at(json.load(open(mp))["ts"])
                if tid not in era:
                    continue
                task = era[tid]
            if judge_one(rdir, task, a.model, a.rejudge, rev):
                n += 1
    print(f"== judged {n} results ==")

if __name__ == "__main__":
    main()
