#!/usr/bin/env python3
"""Build the compact, analytical, self-contained HTML benchmark report (light / minimal / professional).

Default (no args) writes ~/Desktop/benchmark-report.html + ~/Desktop/benchmark-report-runs.html (the run
explorer subpage, linked from the header) (+ screenshots copied to ~/Desktop/benchmark-artifacts/).

No external deps: inline CSS + CSS bars. No heatmaps: a single accent highlights the row leader.
Robust to partial results.

Public view: set BENCH_PUBLIC=1 to hide the internal models entirely (the public branch also simply has
no internal result dirs, so they stay hidden even without the flag).

Usage:
  python3 report.py                                   # -> ~/Desktop/benchmark-report.html
  python3 report.py --results DIR --out FILE --artifacts DIR [--title T]   # render any dataset
"""
import os, re, json, shutil, html, datetime, argparse, math
from logos import SM_LOGO, GITHUB_LOGO, PROVIDER_LOGOS
from bench_models import MODELS, LABELS, INTERNAL_MODELS   # roster (branch-specific; public ships no internal)
try:
    from bench import HARNESS as CURRENT_HARNESS           # the current faithful harness stamp
except Exception:
    CURRENT_HARNESS = "isolation-v5"                       # keep in sync with bench.HARNESS
try:
    from judge import JUDGE_MODEL                          # only a fallback: the header prefers the judge
except Exception:                                          # actually recorded in each score.json (judge.py
    JUDGE_MODEL = ""                                       # reads an API key at import, so this can fail)
try:
    from bench_models import DEFAULT_HIDDEN               # label-bases whose toggle starts off (optional)
except ImportError:
    DEFAULT_HIDDEN = set()
try:
    from bench_models import FOCUS_MODELS                 # the only models shown by default (2026-07-08 focus)
except ImportError:
    FOCUS_MODELS = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = "https://github.com/software-mansion-labs/app-control-bench"   # header source link

def _load_versions(fname):   # small versions manifest under configs/ (drops _comment keys)
    try:
        with open(os.path.join(ROOT, "configs", fname)) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except Exception:
        return {}
TOOL_VERSIONS = _load_versions("tool-versions.json")   # {tool: version} — the agents driving the apps
APP_VERSIONS = _load_versions("app-versions.json")     # {app: {version, commit, repo}} — the target surfaces

def mlabel(m): return LABELS.get(m, m)
def is_internal(m): return m in INTERNAL_MODELS   # models hidden from the public view
def default_hidden(m):
    # FOCUS: only the 4 focus SoTA cells (gpt/haiku high/low) show by default; every other model's
    # "show" toggle starts OFF. Falls back to the legacy label-base hide set if FOCUS_MODELS is absent.
    if FOCUS_MODELS is not None:
        return m not in FOCUS_MODELS
    return mlabel(m).split(" (")[0].strip() in DEFAULT_HIDDEN   # toggle starts unchecked

# label-level split: "gpt-5.4-mini (high)" -> base "gpt-5.4-mini" + thinking level "high" ("" if absent).
# The scatter groups/labels dots by display label, so these take a LABEL; base_name/think_level take a key.
def base_of(lab): return str(lab).split(" (")[0].strip()
def think_of(lab):
    s = str(lab)
    return s[s.find("(") + 1:s.rfind(")")].strip() if "(" in s and ")" in s else ""
EFF_RANK = {"no think": 0, "no-think": 0, "none": 0, "low": 1, "med": 2, "medium": 2, "high": 3, "xhigh": 4}
def eff_rank(lab): return EFF_RANK.get(think_of(lab).lower(), 99)  # order of a model's effort ladder

def base_name(m): return base_of(mlabel(m))                        # family name w/o the "(thinking)" suffix
def think_level(m): return think_of(mlabel(m))                     # just the thinking level, e.g. "low"/"med"
def model_groups(model_list):                                      # [(base, [members])], order preserved
    groups, idx = [], {}
    for m in model_list:
        b = base_name(m)
        if b not in idx:
            idx[b] = len(groups); groups.append((b, []))
        groups[idx[b]][1].append(m)
    return groups

def provider_of(m):                               # map a model to a PROVIDER_LOGOS key by its label
    s = mlabel(m).lower()
    if s.startswith(("gpt", "o1", "o3", "o4")) or "openai" in s:        return "openai"
    if any(t in s for t in ("claude", "haiku", "opus", "sonnet")):      return "anthropic"
    if any(t in s for t in ("gemma", "gemini", "palm")):                return "google"
    if "llama" in s or "meta" in s:                                     return "meta"
    if "grok" in s or "xai" in s:                                       return "xai"
    if "mistral" in s or "mixtral" in s:                                return "mistral"
    if "deepseek" in s:                                                 return "deepseek"
    if "qwen" in s:                                                     return "qwen"
    return None
def mlogo(m):                                     # inline provider mark for a model, or '' if unknown
    # the fam-* class is what tints the mark to the parent company's hue (see .mlogo in report.css) —
    # the same class the scatter's dots carry, so one model is one colour everywhere on the page.
    p = provider_of(m)
    return f'<span class="mlogo fam-{p}">{PROVIDER_LOGOS[p]}</span>' if p in PROVIDER_LOGOS else ''
PUBLIC = os.environ.get("BENCH_PUBLIC") == "1"    # public view: hide internal models entirely
TOOLS = ["argent", "agent-device", "none"]
TLABEL = {"none": "no tool"}   # display label for a tool key (else the key itself); `none` = the baseline
def tl_label(tl): return TLABEL.get(tl, tl)
# The no-tool baseline answers "does the tool help at all?", so it stays everywhere it is the comparison
# being made: the hero, the scatter, and the per-TOOL breakdowns (where it is the bar the tools are read
# against). It is excluded only from the per-MODEL breakdowns, where it is not a comparand at all: mixed in,
# it drags each model's average toward the no-tool floor in proportion to how many baseline runs that model
# happens to have, i.e. it ranks models on their run mix rather than on skill.
BASELINE_TOOL = "none"
ANNULLED = set()   # task ids marked annulled in tasks.json — excluded from all scoring, shown as 'annulled'
SCORE = {"success": 1.0, "partial": 0.5, "fail": 0.0, "error": 0.0, None: 0.0}
VORDER = ["success", "partial", "fail", "error", "pending"]
# "error" = the JUDGE failed to return a verdict (API flake), not the agent failing the task. Scoring it
# 0.0 would blame the model for our own infrastructure, so it is excluded from the grade denominator
# exactly like an unjudged run — but it stays visible in the verdict distribution so it can't hide.
# Re-run `python3 runner/judge.py` (no --rejudge) to retry them; valid verdicts are skipped for free.
GRADED = ("success", "partial", "fail")
KIND_LABEL = {"nav": "Navigation", "interact": "Interaction", "post": "Posting"}


def load_tasks():
    d = json.load(open(os.path.join(ROOT, "tasks", "tasks.json")))
    return d["tasks"], {t["id"]: t for t in d["tasks"]}


def parse_chat(path):
    """Compact, embeddable chat from a run's transcript.jsonl: assistant text, reasoning, and tool calls
    (name / input / truncated output / status). Base64 screenshot attachments are dropped (huge). Also
    returns the run's total cost in USD (sum of step_finish costs; 0.0 for local/free models, None if the
    run produced no transcript / no cost fields at all)."""
    out = []
    cost = None
    try:
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            typ = ev.get("type"); part = ev.get("part", {}) or {}
            if typ == "text":
                x = (part.get("text") or "").strip()
                if x:
                    out.append({"k": "t", "x": x, "ts": ev.get("timestamp")})
            elif typ == "reasoning":
                x = (part.get("text") or "").strip()
                if x:
                    out.append({"k": "r", "x": x, "ts": ev.get("timestamp")})
            elif typ == "tool_use":
                st = part.get("state", {}) or {}
                out.append({"k": "u", "n": part.get("tool") or "", "i": st.get("input") or {},
                            "o": str(st.get("output") or "")[:320], "s": st.get("status") or "",
                            "ts": ev.get("timestamp")})
            elif typ == "step_finish":
                c = part.get("cost")
                if c is not None:
                    cost = (cost or 0.0) + c
    except FileNotFoundError:
        pass
    return out, cost


def collect(results_dir):
    rows = {}
    if not os.path.isdir(results_dir):
        return rows
    for cell in os.listdir(results_dir):
        cdir = os.path.join(results_dir, cell)
        if not os.path.isdir(cdir):
            continue
        for tid in os.listdir(cdir):
            rdir = os.path.join(cdir, tid)
            mp = os.path.join(rdir, "meta.json")
            if not os.path.exists(mp):
                continue
            meta = json.load(open(mp))
            sp = os.path.join(rdir, "score.json")
            score = json.load(open(sp)) if (os.path.exists(sp) and os.path.getsize(sp)) else {}
            png = os.path.join(rdir, "final.png")
            chat, cost = parse_chat(os.path.join(rdir, "transcript.jsonl"))
            polluted = (meta.get("versions") or {}).get("harness") != CURRENT_HARNESS
            ji = score.get("judge_input")   # judge telemetry: EXACTLY what the judge was shown. Absent on
            if not isinstance(ji, dict):    # every score.json written before judge.py started recording it
                ji = None                   # — the modal then reconstructs it from tasks.json + meta.json.
            rows[(meta["model"], meta["tool"], tid)] = {
                "meta": meta, "verdict": score.get("verdict"),
                "judge": score.get("judge_model"),   # the model that actually scored THIS run
                "reason": score.get("reason", ""), "conf": score.get("confidence"),
                "judge_input": ji,
                "png": png if os.path.exists(png) else None,
                "chat": chat, "cost": cost,
                # A result produced by an older/polluted harness (pre-skill v1, always-inject v2): shown
                # but slated to be overwritten as soon as the current-harness queue is drained.
                "polluted": polluted}
    return rows


def grade(rows, pred):
    """mean score over scored runs matching pred -> (frac|None, n_scored). 'error' is a judge failure,
    not an agent failure — excluded from the denominator rather than counted as a 0.0 (see GRADED)."""
    vals = [SCORE.get(r["verdict"], 0.0) for k, r in rows.items()
            if pred(k, r) and r["verdict"] in GRADED and not r.get("polluted") and k[2] not in ANNULLED]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def vdist(rows, pred):
    d = {v: 0 for v in VORDER if v != "pending"}
    for k, r in rows.items():
        if pred(k, r) and r["verdict"] and not r.get("polluted") and k[2] not in ANNULLED:
            d[r["verdict"]] = d.get(r["verdict"], 0) + 1
    return d


def pct(p):
    return f"{p*100:.0f}%" if p is not None else "n/a"


def avg_time_success(rows, pred):
    """mean wall_s over SUCCESS-verdict runs matching pred. Failures, partials, errors, timeouts and
    unjudged runs are all excluded — this is 'how long the model takes when it actually succeeds'."""
    vals = [(r["meta"].get("wall_s") or 0.0) for k, r in rows.items()
            if pred(k, r) and r["verdict"] == "success" and not r.get("polluted") and k[2] not in ANNULLED]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def fmt_time(s):
    if s is None:
        return "n/a"
    s = int(round(s))
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def avg_price(rows, pred):
    """mean total run cost in USD over JUDGED runs matching pred (same run set as the success rate, so a
    model's price and success line up). Runs without cost data are skipped; local/free models average to
    $0. -> (mean|None, n)."""
    vals = [r["cost"] for k, r in rows.items()
            if pred(k, r) and r["verdict"] and k[2] not in ANNULLED and r.get("cost") is not None]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def fmt_price(p):
    if p is None:
        return "n/a"
    if p <= 0:
        return "free"
    return f"${p:.2f}"


def _nice_ticks(mx, n=5):
    """~n round-number ticks from 0 up to at least mx; the last tick is the axis max."""
    if mx <= 0:
        return [0, 1]
    raw = mx / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    ticks, t = [], 0.0
    while t < mx:
        ticks.append(round(t, 6))
        t += step
    ticks.append(round(t, 6))
    return ticks


def blab(lab, logo=""):
    """label cell of a horizontal bar row. `logo` is trusted inline HTML (a provider mark) or ''."""
    return f'<span class="blab">{logo}<span class="bname">{html.escape(str(lab))}</span></span>'


def time_bars(title, items):
    """bar panel for avg time. items: (label, seconds|None, n[, logo]). Bars scaled to this panel's max,
    so each tool is compared within itself (argent and agent-device live on very different scales); the
    absolute time on each bar carries the cross-tool comparison. Fastest bar gets the accent."""
    items = sorted(items, key=lambda it: (it[1] is None, it[1] or 0))   # fastest first (low = good), n/a last
    vals = [it[1] for it in items if it[1]]
    mx, mn = (max(vals), min(vals)) if vals else (None, None)
    out = []
    for lab, s, n, *rest in items:
        logo = rest[0] if rest else ""
        if not s:
            out.append(f'<div class="brow">{blab(lab, logo)}'
                       f'<span class="btrack"></span><span class="bval na">n/a</span></div>')
            continue
        w = max(s / mx * 100, 2.0) if mx else 2.0
        win = s == mn  # fastest = best
        out.append(f'<div class="brow">{blab(lab, logo)}'
                   f'<span class="btrack"><span class="bfill{" win" if win else ""}" style="width:{w:.1f}%"></span></span>'
                   f'<span class="bval">{fmt_time(s)}<i> n{n}</i></span></div>')
    return (f'<section class="panel"><h3>{html.escape(title)}</h3>'
            f'<div class="bars">{"".join(out)}</div></section>')


def panel_bars(title, items):
    """compact bar panel. items: (label, frac|None, n[, logo]). leader bar gets the accent; rest neutral."""
    items = sorted(items, key=lambda it: (it[1] is None, -(it[1] or 0)))   # best (highest) first, n/a last
    vals = [it[1] for it in items if it[1] is not None]
    best = max(vals) if vals else None
    out = []
    for lab, frac, n, *rest in items:
        logo = rest[0] if rest else ""
        if frac is None:
            out.append(f'<div class="brow">{blab(lab, logo)}'
                       f'<span class="btrack"></span><span class="bval na">n/a</span></div>')
            continue
        win = best is not None and best > 0 and frac == best
        cls = "bfill win" if win else "bfill"
        vcls = "bval win" if win else "bval"
        out.append(
            f'<div class="brow">{blab(lab, logo)}'
            f'<span class="btrack"><span class="{cls}" style="width:{max(frac*100,1.2):.1f}%"></span></span>'
            f'<span class="{vcls}">{frac*100:.0f}<i>%</i><b>({n})</b></span></div>')
    return (f'<section class="panel"><h3>{html.escape(title)}</h3>'
            f'<div class="bars">{"".join(out)}</div></section>')


def time_bars_vertical(title, items):
    """vertical column chart of avg time per model — used for the per-tool panels (argent, agent-device).
    items: (label, seconds|None, n). Columns scaled to the panel max; fastest column accented."""
    items = sorted(items, key=lambda it: (it[1] is None, it[1] or 0))   # fastest left (low = good), n/a last
    vals = [s for _, s, _ in items if s]
    mx, mn = (max(vals), min(vals)) if vals else (None, None)
    cols = []
    for lab, s, n in items:
        le = html.escape(str(lab))
        if not s:
            cols.append(f'<div class="vcol" title="no successful runs"><span class="vval na">n/a</span>'
                        f'<div class="vbar na"></div><span class="vlab">{le}</span></div>')
            continue
        h = max(s / mx * 100, 2.0) if mx else 2.0
        w = " win" if s == mn else ""
        cols.append(f'<div class="vcol" title="{fmt_time(s)} avg · {n} successful run(s)">'
                    f'<span class="vval">{fmt_time(s)}</span>'
                    f'<div class="vbar{w}" style="height:{h:.1f}%"></div>'
                    f'<span class="vlab{w}">{le}</span></div>')
    return (f'<section class="panel"><h3>{html.escape(title)}</h3>'
            f'<div class="vbars">{"".join(cols)}</div></section>')


def panel_bars_vertical(title, items):
    """vertical bar chart for a success-rate breakdown (e.g. by tool). items: (label, frac|None, n).
    Height ∝ frac (0..100%); leader accented."""
    items = sorted(items, key=lambda it: (it[1] is None, -(it[1] or 0)))   # best (highest) left, n/a last
    vals = [f for _, f, _ in items if f is not None]
    best = max(vals) if vals else None
    cols = []
    for lab, frac, n in items:
        le = html.escape(str(lab))
        if frac is None:
            cols.append(f'<div class="vcol"><span class="vval na">n/a</span>'
                        f'<div class="vbar na"></div><span class="vlab">{le}</span></div>')
            continue
        w = " win" if best and frac == best else ""
        cols.append(f'<div class="vcol" title="{frac*100:.0f}% · {n} scored">'
                    f'<span class="vval">{frac*100:.0f}%</span>'
                    f'<div class="vbar{w}" style="height:{max(frac*100, 2.0):.1f}%"></div>'
                    f'<span class="vlab{w}">{le}</span></div>')
    return (f'<section class="panel"><h3>{html.escape(title)}</h3>'
            f'<div class="vbars">{"".join(cols)}</div></section>')


TOOL_MARK = {"argent": "circle", "agent-device": "square", "none": "diamond"}   # scatter marker per tool


def _marker(shape, cx, cy, cls="pt", attrs=""):
    """One scatter marker. The SHAPE carries the tool; the family colour rides on a `fam-*` class
    (styled in report.css), so no colour is written into the markup."""
    if shape == "square":
        return (f'<rect class="{cls}" {attrs} x="{cx-4.3:.1f}" y="{cy-4.3:.1f}" '
                f'width="8.6" height="8.6" rx="1"></rect>')
    if shape == "diamond":
        return (f'<path class="{cls}" {attrs} d="M{cx:.1f} {cy-6.2:.1f}L{cx+6.2:.1f} {cy:.1f}'
                f'L{cx:.1f} {cy+6.2:.1f}L{cx-6.2:.1f} {cy:.1f}Z"></path>')
    return f'<circle class="{cls}" {attrs} cx="{cx:.1f}" cy="{cy:.1f}" r="5"></circle>'


def _fam_variants(pts):
    """First-appearance index of each family's base models over the given points -> {(fam, base): 0|1|2}.
    v0 (index 0, no class) is the family's own hue; v1/v2 step lightness within the family so a second or
    third model sharing a family stays distinguishable once toggled into view together. A fourth+ model in
    one family reuses v2 (the roster maxes at two per family)."""
    order = {}
    for lab, _tl, _pr, _sc, _n, fam in pts:
        if not fam:
            continue
        b = base_of(lab)
        lst = order.setdefault(fam, [])
        if b not in lst:
            lst.append(b)
    return {(fam, b): min(i, 2) for fam, bases in order.items() for i, b in enumerate(bases)}


def _fam_cls(fam, base, variants):
    """CSS class carrying a dot/connector's colour: 'fam-<x>[ v1|v2]', or '' with no known family."""
    if not fam:
        return ""
    v = variants.get((fam, base), 0)
    return f"fam-{fam}" + (f" v{v}" if v else "")


def _scatter_layer(dots, variants):
    """Connectors + markers for one set of already-positioned dots (cx/cy already resolved through
    whichever X/Y)."""
    out = []
    groups, order = {}, []                                   # (base model, TOOL) -> its own connector
    for d in dots:
        k = (base_of(d["lab"]), d["tool"])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(d)
    for k in order:
        members = groups[k]
        if len(members) < 2:                                 # a lone dot gets no connector (no arrow either)
            continue
        ordered = sorted(members, key=lambda m: (eff_rank(m["lab"]), m["cx"]))
        path = " ".join(f'{m["cx"]:.1f},{m["cy"]:.1f}' for m in ordered)
        fam, base = ordered[0]["fam"], base_of(ordered[0]["lab"])
        cls = _fam_cls(fam, base, variants)
        tool = ordered[0]["tool"]
        pair = html.escape(f"{base}|{tool}", quote=True)
        model_attr, tool_attr = html.escape(base, quote=True), html.escape(tool, quote=True)
        pair_attrs = f'data-pair="{pair}" data-model="{model_attr}" data-tool="{tool_attr}"'
        label = html.escape(f"Select {base} × {tl_label(tool)}", quote=True)
        tip = html.escape(f"{base} × {tl_label(tool)}", quote=True)
        attrs = (f'{pair_attrs} data-scatter-tip="{tip}" role="button" '
                 f'tabindex="0" aria-pressed="false" aria-label="{label}"')
        out.append(f'<polyline class="ptline{f" {cls}" if cls else ""}" {pair_attrs} aria-hidden="true" points="{path}"/>')
        out.append(f'<polyline class="ptline-hit" {attrs} points="{path}"/>')
    for d in dots:
        think = think_of(d["lab"]) or "no think"
        tip = html.escape(f"{think} — {fmt_price(d['pr'])} · {pct(d['sc'])}", quote=True)
        cls = _fam_cls(d["fam"], base_of(d["lab"]), variants)
        base = base_of(d["lab"])
        pair = html.escape(f"{base}|{d['tool']}", quote=True)
        model_attr, tool_attr = html.escape(base, quote=True), html.escape(d["tool"], quote=True)
        label = html.escape(f"Select {base} × {tl_label(d['tool'])}", quote=True)
        attrs = (f'data-pair="{pair}" data-model="{model_attr}" data-tool="{tool_attr}" '
                 f'data-scatter-tip="{tip}" data-cx="{d["cx"]:.1f}" data-cy="{d["cy"]:.1f}" role="button" '
                 f'tabindex="0" aria-pressed="false" aria-label="{label}"')
        out.append(_marker(TOOL_MARK.get(d["tool"], "circle"), d["cx"], d["cy"],
                           cls=f'pt {cls}' if cls else "pt", attrs=attrs))
    return "".join(out)


def _scatter_legend(points):
    """Text-only, selectable HTML legend kept outside the plot SVG."""
    pts = [(str(lab), tl, pr, sc, n, fam) for (lab, tl, pr, sc, n, fam) in points
           if pr is not None and sc is not None]
    model_bases = []
    for lab, _tl, _pr, _sc, _n, _fam in pts:
        base = base_of(lab)
        if base not in model_bases:
            model_bases.append(base)
    # the swatch is not decoration: the plot encodes MODEL as the family hue, so without it the legend
    # names the series without saying which dots are which. Same class the dots carry, hence same colour.
    models = [f'<button type="button" class="scatterlegend-item" data-legend-model="{html.escape(base, quote=True)}" '
              f'aria-pressed="false"><i class="legend-swatch fam-{provider_of(base) or "none"}"></i>'
              f'{html.escape(base)}</button>' for base in model_bases]
    tools = [f'<button type="button" class="scatterlegend-item" data-legend-tool="{html.escape(tl, quote=True)}" '
             f'aria-pressed="false">{html.escape(tl_label(tl))}</button>'
             for tl in TOOLS if any(p[1] == tl for p in pts)]
    return ('<div class="scatterlegend" aria-label="Scatterplot legend">'
            '<div class="scatterlegend-group"><span class="scatterlegend-title">model</span>'
            f'<div class="scatterlegend-items">{"".join(models)}</div></div>'
            '<div class="scatterlegend-group"><span class="scatterlegend-title">tool</span>'
            f'<div class="scatterlegend-items">{"".join(tools)}</div></div></div>')


def scatter_svg(points, scale_points=None):
    """Inline-SVG scatter: x = avg price / task ($), y = completion score (%). One dot per MODEL × TOOL — a
    model's argent / agent-device / no-tool runs are separate dots (shape = tool, colour = model), never
    collapsed into one; a solid line joins the thinking-effort variants of one model ON ONE TOOL. The sweet
    spot is the top-left corner (cheap + high success). points: (label, tool, price|None, success|None, n,
    family). The x-axis is stable across model toggles: its domain comes from `scale_points`, the complete
    report dataset, with right-side marker padding."""
    pts = [(str(lab), tl, pr, sc, n, fam) for (lab, tl, pr, sc, n, fam) in points
           if pr is not None and sc is not None]
    scale_pts = [(str(lab), tl, pr, sc, n, fam) for (lab, tl, pr, sc, n, fam) in (scale_points or points)
                 if pr is not None and sc is not None]
    W, H, ML, MR, MT, MB = 680, 424, 62, 26, 18, 52
    pw, ph = W - ML - MR, H - MT - MB
    maxp = max((p for _, _, p, _, _, _ in scale_pts), default=0)
    xticks = _nice_ticks(maxp * 1.1)
    axmax = xticks[-1] or 1
    X = lambda p: ML + (p / axmax) * pw
    Y = lambda sv: MT + (1 - sv) * ph
    dots = [{"lab": lab, "tool": tl, "pr": pr, "sc": sc, "n": n, "fam": fam, "cx": X(pr), "cy": Y(sc)}
            for lab, tl, pr, sc, n, fam in pts]
    variants = _fam_variants(pts)

    ax = []                                                  # gridlines, ticks, axis lines/titles
    for yv in (0, .25, .5, .75, 1.0):
        y = Y(yv)
        ax.append(f'<line class="sg" x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}"/>')
        ax.append(f'<text class="stk" x="{ML-9}" y="{y+3.6:.1f}" text-anchor="end">{int(yv*100)}</text>')
    for xv in xticks:
        x = X(xv)
        ax.append(f'<line class="sg" x1="{x:.1f}" y1="{MT}" x2="{x:.1f}" y2="{MT+ph}"/>')
        ax.append(f'<text class="stk" x="{x:.1f}" y="{MT+ph+18:.1f}" text-anchor="middle">{html.escape(fmt_price(xv))}</text>')
    ax.append(f'<line class="sax-l" x1="{ML}" y1="{MT}" x2="{ML}" y2="{MT+ph}"/>')           # y axis
    ax.append(f'<line class="sax-l" x1="{ML}" y1="{MT+ph}" x2="{W-MR}" y2="{MT+ph}"/>')      # x axis
    ax.append(f'<text class="sax" x="{ML+pw/2:.0f}" y="{H-9}" text-anchor="middle">avg price per task</text>')
    ax.append(f'<text class="sax" transform="translate(15,{MT+ph/2:.0f}) rotate(-90)" text-anchor="middle">completion score (%)</text>')

    main_layer = _scatter_layer(dots, variants)

    return (f'<svg viewBox="0 0 {W} {H}" class="scatter" preserveAspectRatio="xMidYMid meet" role="group" '
            f'aria-label="price per task vs completion score by model and tool">'
            f'{"".join(ax)}{main_layer}</svg>')


def price_scatter(points, scale_points=None):
    return (f'<div id="stat-scatter">{_scatter_legend(points)}'
            f'<section class="panel"><div class="scatterwrap">{scatter_svg(points, scale_points)}'
            '</div></section></div>')


def mt_cell(p, n, win, frac=0.0):   # frac = score / row-set top score, drives the optional heatmap tweak
    if p is None and n == 0:
        return '<td class="mc empty">pending</td>'
    cls = "mc win" if win else "mc"
    return (f'<td class="{cls}" style="--hm:{frac:.3f}"><span class="mp">{pct(p)}</span>'
            f'<span class="mn">n={n}</span></td>')

def ovc_cell(p, n, extra=""):   # overall-column / summary cell (muted); extra e.g. " grand"
    return f'<td class="mc ovc{extra}"><span class="mp">{pct(p)}</span><span class="mn">n={n}</span></td>'


def sec(title, explain=""):
    """A section header. With an `explain` string the methodology caption collapses into an [i] beside
    the title that opens it on hover or focus; report.css prints the same copy inline (.sub.explain)
    below 768px, where nothing hovers.
    `title` is literal; `explain` is trusted (any dynamic bit is escaped by the caller).
    Mirrors web/src/ui/Chrome.tsx's SectionHeader, which shares this markup's stylesheet."""
    if not explain:
        return f'<div class="sec"><header><h2>{title}</h2></header></div>'
    tip = "explain-" + re.sub(r"[^a-z0-9]+", "-", title.lower())
    return (f'<div class="sec"><header><h2>{title}</h2>'
            f'<span class="ehint"><button type="button" class="eii" '
            f'aria-label="What {title} shows" aria-describedby="{tip}">i</button>'
            f'<span class="etip" role="tooltip" id="{tip}">{explain}</span></span></header>'
            f'<div class="sub explain">{explain}</div></div>')


def nav_bar(page, report_href, explorer_href):
    """Site header: brand mark (links home) + primary nav. `page` ('report'|'explorer') marks the
    current page so its link gets .is-active / aria-current — the two pages are separate files."""
    brand = f'<div class="brand"><span class="bl bl-sm">{SM_LOGO}</span></div>'
    def link(href, label, key):
        cur = ' class="nav-link is-active" aria-current="page"' if page == key else ' class="nav-link"'
        return f'<a href="{href}"{cur}>{label}</a>'
    return ('<nav class="site-nav" aria-label="Primary">'
            f'<a class="nav-brand" href="{report_href}" aria-label="AppControlBench home">{brand}</a>'
            '<div class="nav-links">'
            + link(report_href, "Report", "report")
            + link(explorer_href, "Run explorer", "explorer")
            + '</div></nav>')


# The report is ONE self-contained HTML file (no external requests — see docs/reporting.md), so the
# stylesheets are read off disk and inlined into <style> at build time. They are real .css files, not
# Python strings, so they stay editable as CSS and there is exactly one place any raw value lives:
#   theme.css   — every colour, font, size, shadow, z-index in the project. THE source of truth.
#   report.css  — component styles, var() only. No literals. Enforced by runner/check_theme.py.
_HERE = os.path.dirname(os.path.abspath(__file__))


def _css(name):
    with open(os.path.join(_HERE, name), encoding="utf-8") as f:
        return f.read()


CSS = _css("theme.css") + _css("report.css")


def _optional_text(*parts):
    path = os.path.join(_HERE, *parts)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


GPU_JS = _optional_text("generated", "outcome-atlas.js")


# The methodology's worked examples keep exactly one run in the detail workspace. The index remains
# visible for comparison; selecting a row replaces the detail HTML rather than stacking hidden copies.
METHODOLOGY_JS = r"""
(function(){
  var root=document.getElementById('method-examples');
  if(!root||typeof METHOD_EXAMPLES==='undefined'||!METHOD_EXAMPLES.length) return;
  var index=root.querySelector('.method-task-index'), detail=document.getElementById('method-task-detail');
  var _d=document.createElement('div');
  function esc(s){ _d.textContent=(s==null?'':String(s)); return _d.innerHTML; }
  function chip(r){ return '<span class="method-chip '+esc(r.verdict)+'"><i></i>'+esc(r.verdict)+'</span>'; }
  function render(i){
    var r=METHOD_EXAMPLES[i]||METHOD_EXAMPLES[0];
    index.querySelectorAll('button').forEach(function(b){
      b.setAttribute('aria-current',String(Number(b.dataset.example)===i));
    });
    detail.innerHTML='<article class="method-task-detail '+esc(r.verdict)+'">'
      +'<div class="method-work-hero"><div class="method-hero-copy"><div class="method-detail-meta">'
      +'<span class="method-eyebrow">'+esc(r.id)+'</span><span><b>model</b> '+esc(r.model)+'</span>'
      +'<span><b>tool</b> '+esc(r.tool)+'</span></div>'
      +'<p class="method-prompt">'+esc(r.prompt)+'</p>'
      +'<div class="method-verdict">'+chip(r)+'<span class="method-score">'+esc(r.score)+'</span><span class="method-meta">confidence '+esc(r.confidence)+'</span>'
      +'<p class="method-reason">'+esc(r.reason)+'</p></div></div>'
      +'<div class="method-hero-shot"><img src="'+esc(r.shot)+'" alt="'+esc(r.alt)+'"></div></div></article>';
  }
  index.addEventListener('click',function(e){
    var b=e.target.closest('button[data-example]'); if(!b) return; render(Number(b.dataset.example));
  });
  render(0);
})();
"""

SCROLL_JS = r"""
(function(){
  document.querySelectorAll('.detail-wrap').forEach(function(w){
    var sc = w.querySelector('.detail'); if(!sc) return;
    function upd(){
      var max = sc.scrollWidth - sc.clientWidth;
      w.classList.toggle('sx-left',  sc.scrollLeft > 1);
      w.classList.toggle('sx-right', sc.scrollLeft < max - 1);
    }
    sc.addEventListener('scroll', upd, {passive:true});
    window.addEventListener('resize', upd);
    upd();
  });
})();
"""

APP_GROUP_JS = r"""
(function(){
  document.querySelectorAll('.detail tr.grp').forEach(function(group){
    var expanded=group.dataset.app==='bluesky';
    function setExpanded(value){
      expanded=value;
      group.setAttribute('aria-expanded',String(expanded));
      var marker=group.querySelector('.app-disclosure');
      if(marker) marker.textContent=expanded?'▾':'▸';
      var row=group.nextElementSibling;
      while(row&&!row.classList.contains('grp')){
        if(row.classList.contains('taskrow')) row.hidden=!expanded;
        row=row.nextElementSibling;
      }
    }
    group.addEventListener('click',function(){ setExpanded(!expanded); });
    group.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); setExpanded(!expanded); }
    });
    setExpanded(expanded);
  });
})();
"""

DRAWER_JS = r"""
(function(){
  var drawer=document.getElementById('rundrawer'); if(!drawer) return;
  var title=document.getElementById('dtitle');
  var closeButton=document.getElementById('dclose');
  var body=document.getElementById('mbody'), shot=document.getElementById('mshot');
  var viewToggle=document.getElementById('mview');
  var info=document.getElementById('minfo');
  var LAB=(typeof Q_MLABELS!=='undefined')?Q_MLABELS:{};
  var CH  =(typeof Q_CHATS!=='undefined')?Q_CHATS:{};
  var RUNS=(typeof Q_RUNS!=='undefined')?Q_RUNS:{};
  var TI  =(typeof Q_TASKINFO!=='undefined')?Q_TASKINFO:{};
  function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
  function ft(s){ if(s==null)return 'n/a'; s=Math.round(s); return s<60?s+'s':Math.floor(s/60)+'m '+String(s%60).padStart(2,'0')+'s'; }
  function fp(p){ if(p==null)return 'n/a'; if(p<=0)return 'free'; return p<0.10?'$'+p.toFixed(3):'$'+p.toFixed(2); }
  function sec(head,inner,src){ return '<section class="jsec"><h4>'+head
    +(src?'<span class="jsrc">'+esc(src)+'</span>':'')+'</h4>'+inner+'</section>'; }
  function fact(k,v,na){ return '<div class="jrow"><span class="jk">'+esc(k)+'</span>'
    +'<span class="jv'+(na?' na':'')+'">'+v+'</span></div>'; }
  function quote(s){ return s?'<div class="jquote">'+esc(s)+'</div>'
                            :'<div class="jquote na">not recorded</div>'; }
  // the run's chip, mirroring the grid: a judged run shows its verdict, an unjudged one its DAG state
  var STCHIP={completed:['v-scoring','scoring'], stale:['v-stale','stale'], pending:['v-error','no run']};
  function chip(r){
    if(r.st==='judged'&&r.v) return '<span class="v v-'+esc(r.v)+'"><i></i>'+esc(r.v)+'</span>';
    var c=STCHIP[r.st]||['v-error','unjudged'];
    return '<span class="v '+c[0]+'"><i></i>'+c[1]+'</span>';
  }
  function setRightView(showShot){
    showShot=showShot&&!!shot.querySelector('img');
    body.hidden=showShot;
    shot.hidden=!showShot;
    viewToggle.setAttribute('aria-pressed',showShot?'true':'false');
    viewToggle.textContent=showShot?'Show agent steps':'Show final screenshot';
    body.scrollTop=0;
  }
  function focusToolCall(number){
    setRightView(false);
    var target=body.querySelector('details.msg.tool[data-tool-call="'+number+'"]');
    if(!target) return;
    target.open=true;
    var bodyRect=body.getBoundingClientRect(), targetRect=target.getBoundingClientRect();
    var top=body.scrollTop+targetRect.top-bodyRect.top-(body.clientHeight-targetRect.height)/2;
    var reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    body.scrollTo({top:Math.max(0,top),behavior:reduce?'auto':'smooth'});
    var summary=target.querySelector('summary');
    if(summary){
      try{ summary.focus({preventScroll:true}); }catch(_){ summary.focus(); }
    }
  }
  // LEFT column: compact run facts first, followed by the task, outcome, and ordered tool calls.
  // Prefer score.json's recorded judge_input; older runs fall back to tasks.json plus run metadata.
  function renderInfo(key){
    var p=key.split('__'), r=RUNS[key], t=TI[p[2]]||{};
    if(!r){   // no meta.json for this cell (shouldn't happen — the cell wouldn't be clickable) — still
      info.innerHTML='<div class="mempty">no run details for this run</div>'; return;   // never blank
    }
    var ji=r.ji||null;
    var japp    = (ji&&ji.app)           || r.app || t.app || '';
    var jprompt = (ji&&ji.prompt)        || t.prompt || '';
    var jtn     = (ji&&ji.tool_names)    || r.tn || [];
    var jntc    = (ji&&ji.n_tool_calls!=null) ? ji.n_tool_calls : r.ntc;
    var h='';
    h+=sec('run details','<div class="jfacts">'
      + fact('app', esc(japp||'n/a'), !japp)
      + fact('wall time', ft(r.wall)+(r.to?' · <b>timed out</b>':''))
      + fact('cost', fp(r.cost), r.cost==null)
      + '</div>');
    h+=sec('task',quote(jprompt));
    var conf=(r.conf==null)?null:Number(r.conf);
    var cf = conf==null ? fact('confidence','n/a',true)
      : '<div class="jconf"><span class="jck">confidence</span>'
        +'<span class="btrack"><span class="bfill jbar-'+esc(r.v||'error')+'" '
        +'style="width:'+Math.max(conf*100,2).toFixed(0)+'%"></span></span>'
        +'<span class="jcv">'+conf.toFixed(2)+'</span></div>';
    h+=sec('outcome',
        '<div class="jverd">'+chip(r)+'</div>'+cf
      + (r.reason?'<div class="jreason'+(r.v?' v-'+esc(r.v):'')+'">'+esc(r.reason)+'</div>'
                 :'<div class="jquote na">no reason recorded</div>'),
        r.jm||'');
    h+=sec('tool calls',
        fact('n tool calls', jntc==null?'n/a':esc(jntc), jntc==null)
      + (jtn.length
          ? '<div class="jtools">'+jtn.map(function(n,i){
              return '<span class="jtool" role="button" tabindex="0" data-tool-call="'+(i+1)
                +'" aria-label="Go to tool call '+(i+1)+'"><i>'+(i+1)+'</i>'+esc(n)+'</span>'; }).join('')+'</div>'
          : '<div class="jtools na">(none)</div>'));
    info.innerHTML=h; info.scrollTop=0;
    info.querySelectorAll('.jtool[data-tool-call]').forEach(function(tag){
      tag.addEventListener('click',function(){ focusToolCall(tag.dataset.toolCall); });
      tag.addEventListener('keydown',function(e){
        if(e.key==='Enter'||e.key===' '){ e.preventDefault(); focusToolCall(tag.dataset.toolCall); }
      });
    });
  }
  function commandName(input){
    var raw=input&&typeof input.command==='string'?input.command.trim():'';
    if(!raw) return '';
    var first=raw.split(/&&|\|\||;|\n/)[0].trim().split(/\s+/);
    return first.slice(0,Math.min(first.length,2)).join(' ');
  }
  // RIGHT column: assistant text remains fully visible. Tool inputs and outputs use progressive
  // disclosure, with the final verification output expanded when the transcript identifies one.
  function render(key){
    var p=key.split('__'), chat=CH[key], r=RUNS[key]||{};
    title.innerHTML='<b>'+esc(LAB[p[0]]||p[0])+'</b> · '+esc(p[1])+' · '+esc(p[2]);
    renderInfo(key);
    shot.replaceChildren();
    if(r.shot){
      var img=document.createElement('img');
      img.src=r.shot;
      img.alt='Final screenshot for '+(LAB[p[0]]||p[0])+' · '+p[1]+' · '+p[2];
      shot.appendChild(img);
    }
    viewToggle.disabled=!r.shot;
    viewToggle.title=r.shot?'Toggle between agent steps and the final screenshot':'No final screenshot for this run';
    setRightView(false);
    if(!chat||!chat.length){ body.innerHTML='<div class="mempty">no transcript for this run</div>'; return; }
    var h='', lastTool=-1, toolNumber=0;
    chat.forEach(function(e,i){ if(e.k==='u') lastTool=i; });
    chat.forEach(function(e,i){
      if(e.k==='t'){ h+='<div class="msg assistant"><div class="mrole">assistant</div><div class="mtext">'+esc(e.x)+'</div></div>'; }
      else if(e.k==='r'){ h+='<details class="msg reasoning"><summary class="mrole">reasoning</summary>'
        +'<div class="mtext">'+esc(e.x)+'</div></details>'; }
      else if(e.k==='u'){
        toolNumber+=1;
        var inp=''; try{ inp=JSON.stringify(e.i); }catch(_){ }
        var input=e.i||{}, desc=typeof input.description==='string'?input.description.trim():'';
        var cmd=commandName(input);
        var compact=desc||(e.n==='skill'&&input.name?'Load '+input.name+' workflow':'')||cmd||'No description';
        var verify=i===lastTool && /verify|final|confirm|snapshot|screenshot/i.test(desc+' '+cmd);
        var failed=(e.s&&e.s!=='completed')||/^\s*(error|failed|exception)\b/i.test(e.o||'');
        var raw=(inp&&inp!=='{}'?'input\n'+inp:'')+((inp&&inp!=='{}')&&e.o?'\n\n':'')+(e.o?'output\n'+e.o:'');
        var st=failed?'<span class="tstate">'+esc(e.s&&e.s!=='completed'?e.s:'error')+'</span>':'';
        h+='<details class="msg tool" data-tool-call="'+toolNumber+'"'+(verify||failed?' open':'')
          +'><summary class="trow">'
          +'<span class="tname">'+esc(e.n)+'</span>'
          +(compact?'<span class="targs">'+esc(compact)+'</span>':'')
          +st+'<span class="targs" style="margin-left:auto">'+toolNumber+'</span></summary>'
          +(raw?'<div class="tout">'+esc(raw)+'</div>':'')+'</details>';
      }
    });
    body.innerHTML=h; body.scrollTop=0;
  }
  viewToggle.addEventListener('click',function(){ setRightView(!body.hidden); });
  var trigger=null;
  function open(key,td){ render(key); trigger=td; drawer.hidden=false; }
  function close(returnFocus){
    drawer.hidden=true;
    if(returnFocus&&trigger) trigger.focus();
  }
  closeButton.addEventListener('click',function(){ close(true); });
  document.addEventListener('click',function(e){
    if(drawer.hidden||drawer.contains(e.target)||e.target.closest('td.dc.has-run')) return;
    close(false);
  });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape'&&!drawer.hidden) close(true); });
  document.querySelectorAll('td.dc').forEach(function(td){
    var key=(td.dataset.model||'')+'__'+(td.dataset.tool||'')+'__'+(td.dataset.task||'');
    if(!CH[key]) return;                       // no transcript (pending / annulled) -> not clickable
    td.classList.add('has-run');
    td.tabIndex=0;
    td.addEventListener('click', function(){ open(key,td); });
    td.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); open(key,td); }
    });
  });
})();
"""


# PROTOTYPE — Three structural, read-only comparison layouts on the existing run explorer.
# Switch with ?prototype=compare&variant=a|b|c. This answers the no-horizontal-scroll question and
# is intentionally isolated from the normal explorer until a direction is chosen.
COMPARE_PROTOTYPE_JS = r"""
(function(){
  var query=new URLSearchParams(location.search);
  if(query.get('prototype')!=='compare') return;
  var host=document.getElementById('compare-prototype'), table=document.getElementById('detail-table');
  if(!host||!table) return;
  var escNode=document.createElement('div');
  function esc(value){escNode.textContent=value==null?'':String(value);return escNode.innerHTML;}
  function cfgId(c){return c.model+'__'+c.tool;}
  var configs=[].slice.call(table.querySelectorAll('th.mcol')).map(function(th){
    return {model:th.dataset.model,tool:th.querySelector('.tl').textContent,label:(Q_MLABELS[th.dataset.model]||th.dataset.model)+' / '+th.querySelector('.tl').textContent};
  });
  var tasks=[].slice.call(table.querySelectorAll('tr.taskrow')).map(function(row){
    var task=row.dataset.task, cell=row.querySelector('td.task');
    return {id:task,kind:(cell.querySelector('.tag')||{}).textContent||'',prompt:(cell.querySelector('.tp')||{}).textContent||'',annulled:row.dataset.annulled==='1'};
  });
  if(configs.length<2||!tasks.length) return;
  var selected=configs.slice(0,2), filter='all';
  var names={a:'Wrapped cards',b:'Comparison strips',c:'Outcome lanes'}, variants=['a','b','c'];
  var variant=(query.get('variant')||'a').toLowerCase(); if(variants.indexOf(variant)<0) variant='a';
  document.querySelector('#runs').classList.add('compare-prototype-active'); host.hidden=false;
  function sourceCell(task,cfg){
    return [].slice.call(table.querySelectorAll('td.dc')).find(function(cell){
      return cell.dataset.task===task.id&&cell.dataset.model===cfg.model&&cell.dataset.tool===cfg.tool;
    });
  }
  function result(task,cfg){
    var cell=sourceCell(task,cfg), verdict=task.annulled?'annulled':(cell&&cell.dataset.verdict)||'pending';
    var meta=cell&&cell.querySelector('.csub');
    return {verdict:verdict,meta:meta?meta.textContent.trim():'not run'};
  }
  function chip(r){
    var state=r.verdict==='completed'?'scoring':r.verdict==='pending'?'error':r.verdict;
    var label=r.verdict==='completed'?'scoring':r.verdict==='pending'?'not run':r.verdict;
    return '<span class="v v-'+esc(state)+'"><i></i>'+esc(label)+'</span>';
  }
  function taskMatches(task){
    var statuses=selected.map(function(c){return result(task,c).verdict;});
    if(filter==='different') return statuses.some(function(s){return s!==statuses[0];});
    if(filter==='unjudged') return statuses.some(function(s){return s==='pending'||s==='completed'||s==='stale';});
    return true;
  }
  function visibleTasks(){return tasks.filter(taskMatches);}
  function taskHead(task){return '<div class="cp-task-head"><span class="cp-task-id">'+esc(task.id)+'</span><span class="cp-task-kind">'+esc(task.kind)+'</span><p class="cp-task-prompt">'+esc(task.prompt)+'</p></div>';}
  function stripHead(task){return '<div class="cp-strip-head"><span class="cp-task-id">'+esc(task.id)+'</span><span class="cp-task-kind">'+esc(task.kind)+'</span><p class="cp-task-prompt">'+esc(task.prompt)+'</p></div>';}
  function resultCard(task,cfg){var r=result(task,cfg);return '<button class="cp-result" type="button" data-task="'+esc(task.id)+'" data-config="'+esc(cfgId(cfg))+'"><span><span class="cp-result-name">'+esc(cfg.label)+'</span><span class="cp-result-meta">'+esc(r.meta)+'</span></span>'+chip(r)+'</button>';}
  function variantA(list){
    return list.map(function(task){return '<article class="cp-task">'+taskHead(task)+'<div class="cp-results">'+selected.map(function(c){return resultCard(task,c);}).join('')+'</div></article>';}).join('');
  }
  function variantB(list){
    return list.map(function(task){return '<article class="cp-strip">'+stripHead(task)+'<div class="cp-strip-lines">'
      +selected.map(function(c){var r=result(task,c);return '<button class="cp-line" type="button" data-task="'+esc(task.id)+'" data-config="'+esc(cfgId(c))+'"><span class="cp-line-name">'+esc(c.label)+'</span>'+chip(r)+'<span class="cp-line-meta">'+esc(r.meta)+'</span></button>';}).join('')+'</div></article>';}).join('');
  }
  function lane(task){
    var statuses=selected.map(function(c){return result(task,c).verdict;});
    if(statuses.some(function(s){return s==='pending'||s==='completed'||s==='stale';})) return 'Awaiting results';
    return statuses.every(function(s){return s===statuses[0];})?'Same outcome':'Different outcomes';
  }
  function variantC(list){
    var lanes=['Different outcomes','Same outcome','Awaiting results'];
    return '<div class="cp-lanes">'+lanes.map(function(name){var laneTasks=list.filter(function(t){return lane(t)===name;});return '<section class="cp-lane"><h2>'+name+' <span>'+laneTasks.length+'</span></h2>'
      +laneTasks.map(function(task){return '<article class="cp-lane-task"><h3>'+esc(task.id)+' · '+esc(task.kind)+'</h3><p>'+esc(task.prompt)+'</p><div class="cp-lane-results">'
        +selected.map(function(c){var r=result(task,c);return '<button class="cp-lane-result" type="button" data-task="'+esc(task.id)+'" data-config="'+esc(cfgId(c))+'"><span>'+esc(c.label)+'</span>'+chip(r)+'</button>';}).join('')+'</div></article>';}).join('')+'</section>';}).join('')+'</div>';
  }
  function options(current,index){return configs.map(function(c){var chosen=selected.some(function(s,i){return i!==index&&cfgId(s)===cfgId(c);});return '<option value="'+esc(cfgId(c))+'"'+(cfgId(c)===cfgId(current)?' selected':'')+(chosen?' disabled':'')+'>'+esc(c.label)+'</option>';}).join('');}
  function editor(){
    return '<section class="cp-editor"><div class="cp-editor-top"><span class="cp-kicker">Configurations · order is display order</span><button class="qbtn" type="button" data-action="add" '+(selected.length===configs.length?'disabled':'')+'>+ Add configuration</button></div><div class="cp-slots">'
      +selected.map(function(c,i){return '<div class="cp-slot"><span class="cp-order">'+(i+1)+'</span><select aria-label="Configuration '+(i+1)+'" data-select="'+i+'">'+options(c,i)+'</select><button class="cp-icon" type="button" data-action="up" data-index="'+i+'" '+(i===0?'disabled':'')+' aria-label="Move configuration up">↑</button><button class="cp-icon" type="button" data-action="down" data-index="'+i+'" '+(i===selected.length-1?'disabled':'')+' aria-label="Move configuration down">↓</button><button class="cp-icon" type="button" data-action="remove" data-index="'+i+'" '+(selected.length===1?'disabled':'')+' aria-label="Remove configuration">×</button></div>';}).join('')+'</div></section>';
  }
  function controls(list){return '<div class="cp-filter"><button type="button" data-filter="all" aria-pressed="'+(filter==='all')+'">All tasks</button><button type="button" data-filter="different" aria-pressed="'+(filter==='different')+'">Different outcomes</button><button type="button" data-filter="unjudged" aria-pressed="'+(filter==='unjudged')+'">Awaiting results</button><span>'+list.length+' of '+tasks.length+' tasks</span></div>';}
  function switcher(){var pos=variants.indexOf(variant),before=variants[(pos+variants.length-1)%variants.length],after=variants[(pos+1)%variants.length];return '<nav class="cp-switcher" aria-label="Prototype variations"><button type="button" data-variant="'+before+'" aria-label="Previous variation">←</button><span>'+variant.toUpperCase()+': '+names[variant]+'</span><button type="button" data-variant="'+after+'" aria-label="Next variation">→</button></nav>';}
  function render(){
    var list=visibleTasks(), body=variant==='a'?variantA(list):variant==='b'?variantB(list):variantC(list);
    host.innerHTML='<header class="cp-head"><div><div class="cp-kicker">Throwaway prototype · run explorer</div><h1>Compare configurations</h1><p>Pick any configurations; results wrap instead of widening the page.</p></div><div class="cp-state">'+selected.length+' selected · '+names[variant]+'</div></header>'+editor()+controls(list)+'<div class="cp-body">'+body+'</div>'+switcher();
  }
  function setVariant(next){variant=next;query.set('prototype','compare');query.set('variant',variant);history.replaceState(null,'','?'+query.toString());render();}
  function configById(id){return configs.find(function(c){return cfgId(c)===id;});}
  host.addEventListener('click',function(e){
    var resultButton=e.target.closest('[data-task][data-config]'); if(resultButton){e.stopPropagation();var cfg=configById(resultButton.dataset.config), task=tasks.find(function(t){return t.id===resultButton.dataset.task;}), cell=cfg&&task&&sourceCell(task,cfg);if(cell) cell.click();return;}
    var variantButton=e.target.closest('[data-variant]'); if(variantButton){setVariant(variantButton.dataset.variant);return;}
    var filterButton=e.target.closest('[data-filter]'); if(filterButton){filter=filterButton.dataset.filter;render();return;}
    var action=e.target.closest('[data-action]'); if(!action)return;var index=Number(action.dataset.index), kind=action.dataset.action;
    if(kind==='add'){var add=configs.find(function(c){return !selected.some(function(s){return cfgId(s)===cfgId(c);});});if(add) selected.push(add);}
    if(kind==='remove'&&selected.length>1) selected.splice(index,1);
    if(kind==='up'&&index>0){var up=selected[index-1];selected[index-1]=selected[index];selected[index]=up;}
    if(kind==='down'&&index<selected.length-1){var down=selected[index+1];selected[index+1]=selected[index];selected[index]=down;}
    render();
  });
  host.addEventListener('change',function(e){var select=e.target.closest('select[data-select]');if(!select)return;var replacement=configById(select.value),index=Number(select.dataset.select);if(replacement) selected[index]=replacement;render();});
  document.addEventListener('keydown',function(e){var target=e.target;if(target&&(/INPUT|TEXTAREA|SELECT/.test(target.tagName)||target.isContentEditable))return;if(e.key==='ArrowLeft')setVariant(variants[(variants.indexOf(variant)+variants.length-1)%variants.length]);if(e.key==='ArrowRight')setVariant(variants[(variants.indexOf(variant)+1)%variants.length]);});
  render();
})();
"""


RUN_EXPLORER_JS = r"""
(function(){
  var root=document.getElementById('run-explorer-root');
  if(!root||typeof Q_EXPLORER==='undefined') return;
  var data=Q_EXPLORER, configs=data.configs||[], tasks=data.tasks||[];
  var runs=(typeof Q_RUNS!=='undefined')?Q_RUNS:{};
  var chats=(typeof Q_CHATS!=='undefined')?Q_CHATS:{};
  var cfgById={}, taskById={};
  configs.forEach(function(c){cfgById[c.id]=c;});
  tasks.forEach(function(t){taskById[t.id]=t;});
  var focusKey='appcontrolbench:run-focus:v1';
  var maxFocus=Math.min(6,Math.max(1,Math.floor(((root.clientWidth||window.innerWidth)-376)/144)));
  var storedFocus=[];
  try{storedFocus=JSON.parse(localStorage.getItem(focusKey)||'[]');}catch(_){}
  var focus=storedFocus.filter(function(id,i,a){return cfgById[id]&&a.indexOf(id)===i;}).slice(0,maxFocus);
  if(!focus.length) focus=configs.slice(0,Math.min(5,maxFocus)).map(function(c){return c.id;});
  var filters={query:'',suite:'all',outcome:'all'};
  var selection=null, pickerOpen=false, pinned=false;
  var peekTimer=null, peek=document.getElementById('run-peek'), toast=document.getElementById('run-toast');

  function clean(s){return String(s==null?'':s).replace(/[\u2013\u2014]/g,'-');}
  function esc(s){return clean(s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function key(taskId,configId){return configId+'__'+taskId;}
  function getRun(taskId,configId){return runs[key(taskId,configId)]||null;}
  function stateOf(taskId,configId){
    var r=getRun(taskId,configId);
    if(!r) return 'pending';
    if(r.st==='stale') return 'stale';
    if(r.st==='completed') return 'scoring';
    return r.v||'pending';
  }
  function displayState(taskId,configId){
    return taskById[taskId]&&taskById[taskId].annulled?'annulled':stateOf(taskId,configId);
  }
  function scoreOf(r){return !r?null:r.v==='success'?1:r.v==='partial'?.5:r.v==='fail'?0:null;}
  function fmtTime(s){
    if(s==null) return 'n/a';
    s=Math.round(s);
    return s<60?s+'s':Math.floor(s/60)+'m'+String(s%60).padStart(2,'0')+'s';
  }
  function metricValue(r){
    return r&&r.wall!=null?fmtTime(r.wall):'n/a';
  }
  function metricNumber(r){
    return r&&r.wall!=null?Number(r.wall):null;
  }
  function chip(state){
    var label=state==='scoring'?'scoring':state==='pending'?'not run':state;
    return '<span class="rx-chip rx-chip-'+esc(state)+'"><i></i>'+esc(label)+'</span>';
  }
  function configLabel(c){return c.base;}
  function configSub(c){return (c.effort?'('+c.effort+') / ':'')+c.toolLabel;}
  function configHead(c){
    return '<span class="rx-config-name">'+esc(configLabel(c))+'</span>'
      +'<span class="rx-config-sub">'+esc(configSub(c))+'</span>';
  }
  function saveFocus(){try{localStorage.setItem(focusKey,JSON.stringify(focus));}catch(_){}}
  function announce(message){
    if(!toast) return;
    toast.textContent=clean(message); toast.hidden=false;
    clearTimeout(announce.timer); announce.timer=setTimeout(function(){toast.hidden=true;},2200);
  }
  function querySelection(){
    var q=new URLSearchParams(location.search), level=q.get('level'), taskId=q.get('task'), configId=q.get('config');
    if(!taskById[taskId]||['run','task','field'].indexOf(level)<0) return null;
    if(level==='run'&&!cfgById[configId]) return null;
    return {level:level,taskId:taskId,configId:level==='run'?configId:null};
  }
  function syncUrl(){
    var q=new URLSearchParams(location.search);
    ['level','task','config'].forEach(function(name){q.delete(name);});
    if(selection){
      q.set('level',selection.level); q.set('task',selection.taskId);
      if(selection.configId) q.set('config',selection.configId);
    }
    history.replaceState(null,'',location.pathname+(q.toString()?'?'+q.toString():''));
  }
  function setSelection(next,shouldSync){
    selection=next;
    if(shouldSync!==false) syncUrl();
    render();
    requestAnimationFrame(function(){
      var row=selection&&root.querySelector('.rx-row[data-task="'+CSS.escape(selection.taskId)+'"]');
      if(row) row.scrollIntoView({block:'nearest'});
    });
  }
  function visibleTasks(){
    var q=filters.query.trim().toLowerCase();
    return tasks.filter(function(t){
      if(filters.suite!=='all'&&t.app!==filters.suite) return false;
      if(q&&([t.id,t.kind,t.app,t.prompt].join(' ').toLowerCase().indexOf(q)<0)) return false;
      var states=configs.map(function(c){return stateOf(t.id,c.id);});
      if(filters.outcome==='mixed'){
        var judged=states.filter(function(s){return s==='success'||s==='partial'||s==='fail';});
        if((new Set(judged)).size<2) return false;
      }
      if(filters.outcome==='awaiting'&&!states.some(function(s){return s==='pending'||s==='scoring'||s==='stale';})) return false;
      return true;
    });
  }
  function pickerHtml(){
    if(!pickerOpen) return '';
    var available=configs.filter(function(c){return focus.indexOf(c.id)<0;});
    return '<div class="rx-picker" role="dialog" aria-label="Add a configuration to focus">'
      +'<label>Find configuration<input id="rx-picker-search" type="search" placeholder="model, effort, or harness"></label>'
      +'<div class="rx-picker-list">'+(available.length?available.map(function(c){
        return '<button type="button" data-action="promote" data-config="'+esc(c.id)+'">'
          +'<span>'+configHead(c)+'</span><b>+</b></button>';
      }).join(''):'<p>Every configuration is already in focus.</p>')+'</div></div>';
  }
  function toolbarHtml(){
    return '<div class="rx-toolbar"><span class="rx-toolbar-label">IN FOCUS</span>'
      +'<div class="rx-focus-list">'+focus.map(function(id){
        var c=cfgById[id];
        return '<span class="rx-focus-pill"><span>'+esc(configLabel(c))+' <small>'+esc(configSub(c))+'</small></span>'
          +'<button type="button" data-action="remove-focus" data-config="'+esc(id)+'" aria-label="Remove '+esc(configLabel(c))+' from focus">×</button></span>';
      }).join('')+'</div>'
      +'<div class="rx-add-wrap"><button class="rx-add" type="button" data-action="toggle-picker" aria-expanded="'+pickerOpen+'">+ add from '+configs.length+'</button>'
      +pickerHtml()+'</div></div>';
  }
  function fieldTicks(taskId){
    var rest=configs.filter(function(c){return focus.indexOf(c.id)<0;});
    return rest.map(function(c){return '<i class="rx-tick rx-tick-'+esc(displayState(taskId,c.id))+'"></i>';}).join('');
  }
  function matrixHtml(list){
    var restCount=Math.max(0,configs.length-focus.length);
    var head='<div class="rx-grid rx-column-head"><div>task</div>'
      +focus.map(function(id){return '<div>'+configHead(cfgById[id])+'</div>';}).join('')
      +'<button type="button" data-action="field-help"><span class="rx-config-name">others</span><span class="rx-config-sub" aria-hidden="true">&nbsp;</span></button></div>';
    var rowsHtml=list.map(function(t,index){
      var selected=selection&&selection.taskId===t.id?' is-selected':'';
      return '<div class="rx-grid rx-row'+selected+'" data-task="'+esc(t.id)+'" style="--rx-row-index:'+index+'">'
        +'<button type="button" class="rx-task-cell" data-action="open-task" data-task="'+esc(t.id)+'">'
        +'<span><b>'+esc(t.id)+'</b><small>'+esc(t.kind)+'</small></span><span>'+esc(t.prompt)+'</span></button>'
        +focus.map(function(id){
          var r=getRun(t.id,id), state=displayState(t.id,id);
          return '<button type="button" class="rx-result-cell" data-action="open-run" data-task="'+esc(t.id)
            +'" data-config="'+esc(id)+'" aria-label="'+esc(configLabel(cfgById[id])+' '+state+' '+metricValue(r))+'">'
            +chip(state)+'<span class="rx-cell-metric">'+esc(metricValue(r))+'</span></button>';
        }).join('')
        +'<button type="button" class="rx-field-strip" data-action="open-field" data-task="'+esc(t.id)
          +'" aria-label="Open '+restCount+' configurations outside focus for '+esc(t.id)+'">'
          +'<span>'+fieldTicks(t.id)+'</span></button></div>';
    }).join('');
    if(!list.length) rowsHtml='<div class="rx-empty"><b>No tasks match these filters.</b><button type="button" data-action="reset-filters">Reset filters</button></div>';
    return head+'<div class="rx-rows">'+rowsHtml+'</div>';
  }
  function median(values){
    values=values.filter(function(v){return v!=null&&!Number.isNaN(v);}).sort(function(a,b){return a-b;});
    if(!values.length) return null;
    var mid=Math.floor(values.length/2);
    return values.length%2?values[mid]:(values[mid-1]+values[mid])/2;
  }
  function signed(n){return (n>0?'+':'')+n;}
  function taskStats(taskId){
    var values=configs.map(function(c){return {c:c,r:getRun(taskId,c.id)};});
    var pass=values.filter(function(x){return x.r&&x.r.v==='success';}).length;
    var byTool={};
    values.forEach(function(x){
      var s=scoreOf(x.r); if(s==null) return;
      (byTool[x.c.tool]||(byTool[x.c.tool]=[])).push(s);
    });
    var tools=Object.keys(byTool).map(function(tool){
      var vals=byTool[tool]; return {tool:tool,value:vals.reduce(function(a,b){return a+b;},0)/vals.length};
    }).sort(function(a,b){return a.value-b.value;});
    var worst=tools.length?tools[0]:null, best=tools.length?tools[tools.length-1]:null;
    var effortGroups={};
    values.forEach(function(x){
      var s=scoreOf(x.r); if(s==null||!x.c.effort) return;
      (effortGroups[x.c.effort]||(effortGroups[x.c.effort]=[])).push(s);
    });
    var efforts=Object.keys(effortGroups).sort(function(a,b){return (data.effortRank[a]||99)-(data.effortRank[b]||99);});
    var effortDelta=null;
    if(efforts.length>1){
      var low=effortGroups[efforts[0]], high=effortGroups[efforts[efforts.length-1]];
      effortDelta=Math.round(((high.reduce(function(a,b){return a+b;},0)/high.length)
        -(low.reduce(function(a,b){return a+b;},0)/low.length))*100);
    }
    return {pass:pass,total:configs.length,worst:worst,best:best,effortDelta:effortDelta,
      median:median(values.map(function(x){return metricNumber(x.r);})),values:values};
  }
  function fingerprintHtml(taskId){
    var bases=[], byBase={};
    configs.forEach(function(c){if(!byBase[c.base]){byBase[c.base]=[];bases.push(c.base);}byBase[c.base].push(c);});
    var efforts=[];
    configs.forEach(function(c){if(efforts.indexOf(c.effort)<0) efforts.push(c.effort);});
    efforts.sort(function(a,b){return (data.effortRank[a]||99)-(data.effortRank[b]||99);});
    var axes=[];
    efforts.forEach(function(effort){data.tools.forEach(function(tool){axes.push({effort:effort,tool:tool});});});
    var h='<div class="rx-fingerprint" style="--rx-fp-cols:'+axes.length+'"><div class="rx-fp-head">model</div>';
    axes.forEach(function(axis){h+='<div class="rx-fp-head" title="'+esc(axis.effort+' / '+axis.tool)+'">'+esc(data.toolShort[axis.tool]||axis.tool)+'</div>';});
    bases.forEach(function(base){
      h+='<div class="rx-fp-model">'+esc(base)+'</div>';
      axes.forEach(function(axis){
        var c=(byBase[base]||[]).find(function(item){return item.effort===axis.effort&&item.tool===axis.tool;});
        h+=c?'<button type="button" class="rx-fp-cell rx-tick-'+esc(displayState(taskId,c.id))+'" data-action="open-run" data-task="'+esc(taskId)
          +'" data-config="'+esc(c.id)+'" title="'+esc(configLabel(c)+' '+configSub(c))+'"></button>'
          :'<span class="rx-fp-cell rx-tick-pending"></span>';
      });
    });
    return h+'</div><div class="rx-fp-legend">→ effort × harness&nbsp;&nbsp; / &nbsp;&nbsp;↓ '+bases.length+' models&nbsp;&nbsp; / &nbsp;&nbsp;all '+configs.length+' configurations</div>';
  }
  function byModelHtml(taskId){
    var bases=[], byBase={};
    configs.forEach(function(c){if(!byBase[c.base]){byBase[c.base]=[];bases.push(c.base);}byBase[c.base].push(c);});
    return bases.map(function(base){
      var items=byBase[base], passed=items.filter(function(c){var r=getRun(taskId,c.id);return r&&r.v==='success';}).length;
      return '<div class="rx-model-row"><b>'+esc(base)+'</b><span>'
        +items.map(function(c){return '<i class="rx-model-dot rx-tick-'+esc(displayState(taskId,c.id))+'" title="'+esc(configSub(c))+'"></i>';}).join('')
        +'</span><small>'+passed+'/'+items.length+'</small></div>';
    }).join('');
  }
  function taskPanel(taskId){
    var t=taskById[taskId], stats=taskStats(taskId);
    var worst=stats.worst&&stats.best?esc(data.toolLabels[stats.worst.tool]||stats.worst.tool)+' '+signed(Math.round((stats.worst.value-stats.best.value)*100)):'no judged runs';
    var effort=stats.effortDelta==null?'effort n/a':'effort '+signed(stats.effortDelta);
    var med=stats.median==null?'median n/a':'median '+fmtTime(stats.median);
    return '<div class="rx-panel-head"><div><span class="rx-breadcrumb">'+esc(taskId)+'</span><span class="rx-panel-nav">'
      +panelTaskNav(taskId)+'</span></div><button type="button" data-action="close-panel" aria-label="Close details">×</button></div>'
      +'<div class="rx-panel-scroll"><section class="rx-task-summary"><h2>'+esc(taskId)+'</h2><span>'+esc(t.kind+' / '+t.app)+'</span><p>'+esc(t.prompt)+'</p>'
      +'<div class="rx-summary-chips"><b>'+stats.pass+'/'+stats.total+' pass</b><b class="is-negative">'+worst+'</b>'
      +'<b class="'+(stats.effortDelta!=null&&stats.effortDelta>=0?'is-positive':'is-negative')+'">'+effort+'</b><b>'+esc(med)+'</b></div></section>'
      +'<section class="rx-panel-section">'+fingerprintHtml(taskId)+'</section>'
      +'<section class="rx-panel-section"><header><b>BY MODEL</b></header>'+byModelHtml(taskId)+'</section>'
      +'<div class="rx-panel-actions"><button class="is-primary" type="button" data-action="open-first-failure" data-task="'+esc(taskId)+'">open first failure</button>'
      +'<button type="button" data-action="open-picker">add a config to focus</button></div></div>';
  }
  function panelTaskNav(taskId){
    var i=tasks.findIndex(function(t){return t.id===taskId;});
    return '<button type="button" data-action="move-task" data-delta="-1" '+(i<=0?'disabled':'')+' aria-label="Previous task">‹</button>'
      +'<button type="button" data-action="move-task" data-delta="1" '+(i<0||i>=tasks.length-1?'disabled':'')+' aria-label="Next task">›</button>';
  }
  function stepLabel(entry){
    var input=entry.i||{}, raw=typeof input.description==='string'?input.description.trim():'';
    if(!raw&&typeof input.command==='string') raw=input.command.trim().split(/\n/)[0];
    return raw||entry.n||'tool call';
  }
  // One clipped line: the cap keeps a whole event out of the DOM, the ellipsis marks a cut CSS missed.
  function traceSummary(text){return text.length>160?text.slice(0,160)+'…':text;}
  function traceHtml(chat){
    if(!chat.length) return '<div class="rx-panel-empty">No transcript recorded.</div>';
    return '<div class="rx-trace">'+chat.map(function(entry,i){
      if(entry.k==='u') return '<details><summary><b>'+esc(entry.n||'tool')+'</b><span>'+esc(traceSummary(stepLabel(entry)))+'</span></summary>'
        +'<pre>'+esc(entry.o||'No output recorded')+'</pre></details>';
      return '<details><summary><b>'+esc(entry.k==='r'?'reasoning':'assistant')+'</b><span>'+esc(traceSummary(entry.x||''))+'</span></summary>'
        +'<pre>'+esc(entry.x||'')+'</pre></details>';
    }).join('')+'</div>';
  }
  function runPanel(taskId,configId){
    var c=cfgById[configId], r=getRun(taskId,configId), chat=chats[key(taskId,configId)]||[];
    var tools=chat.filter(function(entry){return entry.k==='u';});
    function timeNumber(value){
      if(typeof value==='number') return value;
      var parsed=Date.parse(value); return Number.isFinite(parsed)?parsed:0;
    }
    var baseTs=chat.map(function(entry){return timeNumber(entry.ts);}).filter(Boolean).sort(function(a,b){return a-b;})[0]||0;
    var state=displayState(taskId,configId), ci=configs.findIndex(function(item){return item.id===configId;});
    var steps=tools.length?tools.map(function(entry,i){
      var stamp=timeNumber(entry.ts), failed=r&&r.v==='fail'&&i===tools.length-1,
          elapsed=baseTs&&stamp?Math.max(0,Math.round((stamp-baseTs)/1000)):null;
      return '<div class="rx-step'+(failed?' is-terminal':'')+'"><span>'+(i+1)+'</span><i></i><b>'+esc(stepLabel(entry))+'</b><small>'+esc(elapsed==null?'':fmtTime(elapsed))+'</small></div>';
    }).join(''):'<div class="rx-panel-empty">No tool-call steps recorded.</div>';
    var media=r&&r.shot?'<img src="'+esc(r.shot)+'" alt="Final screenshot for '+esc(configLabel(c)+' '+taskId)+'">'
      :'<div class="rx-media-placeholder">No final screenshot recorded</div>';
    return '<div class="rx-panel-head"><div><button class="rx-crumb-link" type="button" data-action="open-task" data-task="'+esc(taskId)+'">'+esc(taskId)+'</button>'
      +'<span class="rx-breadcrumb">▸ run</span></div><span class="rx-panel-nav"><button type="button" data-action="move-config" data-delta="-1" '+(ci<=0?'disabled':'')+'>‹</button>'
      +'<button type="button" data-action="move-config" data-delta="1" '+(ci<0||ci>=configs.length-1?'disabled':'')+'>›</button></span>'
      +'<button type="button" data-action="close-panel" aria-label="Close details">×</button></div>'
      +'<div class="rx-modal-cols"><div class="rx-modal-col">'
      +'<section class="rx-run-title"><h2>'+esc(configLabel(c))+'</h2><span>'+esc(configSub(c))+'</span>'
      +'<div>'+chip(state)+'<b>'+esc(metricValue(r))+'</b></div><button type="button" data-action="move-config" data-delta="-1" '+(ci<=0?'disabled':'')+'>‹ prev config</button>'
      +'<span> / </span><button type="button" data-action="move-config" data-delta="1" '+(ci>=configs.length-1?'disabled':'')+'>next config ›</button></section>'
      +'<section class="rx-judge"><b>JUDGE</b><p>'+esc(r&&r.reason?r.reason:'No judge verdict recorded.')+'</p></section>'
      +'<section class="rx-panel-section"><header><b>STEPS</b><span>'+tools.length+' of '+tools.length+'</span></header><div class="rx-steps">'+steps+'</div></section>'
      +'<section class="rx-panel-section"><header><b>FINAL FRAME</b></header><div class="rx-run-media">'+media+'</div></section>'
      +'<div class="rx-panel-actions"><button type="button" data-action="rerun">re-run</button></div></div>'
      +'<div class="rx-modal-col"><section class="rx-panel-section">'
      +'<header><b>FULL TRACE</b><span>'+chat.length+' events</span></header>'+traceHtml(chat)+'</section></div></div>';
  }
  function fieldPanel(taskId){
    var rest=configs.filter(function(c){return focus.indexOf(c.id)<0;});
    var order={fail:0,partial:1,error:2,stale:3,scoring:4,pending:5,success:6};
    function stateRank(configId){
      var state=displayState(taskId,configId);
      return Object.prototype.hasOwnProperty.call(order,state)?order[state]:9;
    }
    rest.sort(function(a,b){return stateRank(a.id)-stateRank(b.id);});
    var rowsHtml=rest.map(function(c){
      var r=getRun(taskId,c.id), state=displayState(taskId,c.id);
      return '<div class="rx-field-row"><button type="button" class="rx-field-main" data-action="open-run" data-task="'+esc(taskId)+'" data-config="'+esc(c.id)+'">'
        +'<span>'+configHead(c)+'</span>'+chip(state)+'<small>'+esc(metricValue(r))+'</small></button>'
        +'<button class="rx-promote" type="button" data-action="promote" data-config="'+esc(c.id)+'" aria-label="Promote '+esc(configLabel(c))+' into focus">+</button></div>';
    }).join('');
    return '<div class="rx-panel-head"><div><span class="rx-crumb-link">'+esc(taskId)+'</span><span class="rx-breadcrumb">▸ the rest</span></div>'
      +'<button type="button" data-action="close-panel" aria-label="Close details">×</button></div>'
      +'<div class="rx-field-title"><h2>'+rest.length+' configurations outside focus</h2><p>results for this task · sorted worst first · <b>'+focus.length+' in focus hidden</b></p></div>'
      +'<div class="rx-field-list">'+(rowsHtml||'<div class="rx-panel-empty">No configurations remain outside focus.</div>')+'</div>'
      +'<div class="rx-panel-footer">a row opens that run · + promotes it into a focus column</div>';
  }
  function panelHtml(){
    if(selection.level==='task') return taskPanel(selection.taskId);
    if(selection.level==='field') return fieldPanel(selection.taskId);
    return runPanel(selection.taskId,selection.configId);
  }
  function controlsHtml(list){
    var suites=[];
    tasks.forEach(function(t){if(suites.indexOf(t.app)<0)suites.push(t.app);});
    return '<div class="rx-filterbar"><label>Search tasks<input id="rx-query" type="search" placeholder="Task id or prompt" value="'+esc(filters.query)+'"></label>'
      +'<label>App suite<select id="rx-suite"><option value="all">all suites</option>'+suites.map(function(s){return '<option value="'+esc(s)+'"'+(filters.suite===s?' selected':'')+'>'+esc(s)+'</option>';}).join('')+'</select></label>'
      +'<label>Show<select id="rx-outcome"><option value="all"'+(filters.outcome==='all'?' selected':'')+'>all outcomes</option><option value="mixed"'+(filters.outcome==='mixed'?' selected':'')+'>mixed outcomes</option><option value="awaiting"'+(filters.outcome==='awaiting'?' selected':'')+'>awaiting results</option></select></label>'
      +'<span>'+list.length+' of '+tasks.length+' tasks</span></div>';
  }
  function render(){
    try{
      var list=visibleTasks(), restCount=Math.max(0,configs.length-focus.length);
      var othersWide=restCount?restCount*4+(restCount-1)*2+16:16;
      var othersCompact=restCount?restCount*3+(restCount-1)*2+16:16;
      document.documentElement.classList.toggle('rx-modal-open',!!selection);
      root.innerHTML=controlsHtml(list)+'<section class="rx-frame"><div class="rx-workspace" style="--rx-focus-count:'+focus.length+';--rx-others-wide:'+othersWide+'px;--rx-task-wide:'+(352-othersWide)+'px;--rx-others-compact:'+othersCompact+'px;--rx-task-compact:'+(274-othersCompact)+'px">'
        +'<div class="rx-table-area">'+toolbarHtml()+'<div class="rx-matrix">'+matrixHtml(list)+'</div></div></div></section>'
        +(selection?'<div class="rx-modal is-'+esc(selection.level)+'">'
          +'<div class="rx-modal-box" role="dialog" aria-modal="true" aria-label="Run explorer details">'+panelHtml()+'</div></div>':'');
      if(pickerOpen){
        var search=document.getElementById('rx-picker-search');
        if(search) requestAnimationFrame(function(){search.focus();});
      }
    }catch(error){
      document.documentElement.classList.remove('rx-modal-open');
      root.innerHTML='<div class="rx-error"><b>Run explorer could not render.</b><pre>'+esc(error&&error.message?error.message:error)+'</pre></div>';
    }
  }
  function promote(configId){
    if(focus.indexOf(configId)>=0) return;
    if(focus.length>=maxFocus){announce('This width fits '+maxFocus+' focus columns. Remove one before adding another.');return;}
    focus.push(configId); saveFocus(); pickerOpen=false; render();
  }
  function removeFocus(configId){
    if(focus.length<=1){announce('Keep at least one configuration in focus.');return;}
    focus=focus.filter(function(id){return id!==configId;}); saveFocus(); render();
  }
  function moveTask(delta){
    if(!selection) return;
    var i=tasks.findIndex(function(t){return t.id===selection.taskId;}), next=tasks[i+delta];
    if(!next) return;
    setSelection({level:selection.level,taskId:next.id,configId:selection.level==='run'?selection.configId:null});
  }
  function moveConfig(delta){
    if(!selection) return;
    var i=configs.findIndex(function(c){return c.id===selection.configId;}), next=configs[i+delta];
    if(!next) return;
    setSelection({level:'run',taskId:selection.taskId,configId:next.id});
  }
  function openFirstFailure(taskId){
    var c=configs.find(function(item){var r=getRun(taskId,item.id);return r&&r.v==='fail';});
    if(c) setSelection({level:'run',taskId:taskId,configId:c.id});
    else announce('No failed run is recorded for this task.');
  }
  root.addEventListener('click',function(e){
    // The scrim is the modal element itself, so only a click that lands on it — not one bubbling out
    // of the box — closes the detail.
    if(e.target.classList.contains('rx-modal')){setSelection(null);return;}
    var button=e.target.closest('[data-action]'); if(!button) return;
    var action=button.dataset.action;
    if(action==='toggle-picker'){pickerOpen=!pickerOpen;render();}
    else if(action==='open-picker'){pickerOpen=true;render();}
    else if(action==='remove-focus') removeFocus(button.dataset.config);
    else if(action==='promote') promote(button.dataset.config);
    else if(action==='open-run') setSelection({level:'run',taskId:button.dataset.task,configId:button.dataset.config});
    else if(action==='open-task') setSelection({level:'task',taskId:button.dataset.task,configId:null});
    else if(action==='open-field') setSelection({level:'field',taskId:button.dataset.task,configId:null});
    else if(action==='close-panel') setSelection(null);
    else if(action==='move-task') moveTask(Number(button.dataset.delta));
    else if(action==='move-config') moveConfig(Number(button.dataset.delta));
    else if(action==='open-first-failure') openFirstFailure(button.dataset.task);
    else if(action==='rerun') announce('Re-run is unavailable in this static report. Use the benchmark runner.');
    else if(action==='field-help') announce('Each field strip opens the configurations outside focus for that task.');
    else if(action==='reset-filters'){filters={query:'',suite:'all',outcome:'all'};render();}
  });
  root.addEventListener('input',function(e){
    if(e.target.id==='rx-query'){
      var start=e.target.selectionStart, end=e.target.selectionEnd;
      filters.query=e.target.value; render();
      requestAnimationFrame(function(){
        var query=document.getElementById('rx-query');
        if(query){query.focus();query.setSelectionRange(start,end);}
      });
    }
    if(e.target.id==='rx-picker-search'){
      var q=e.target.value.toLowerCase();
      root.querySelectorAll('.rx-picker-list button').forEach(function(button){
        button.hidden=button.textContent.toLowerCase().indexOf(q)<0;
      });
    }
  });
  root.addEventListener('change',function(e){
    if(e.target.id==='rx-suite'){filters.suite=e.target.value;render();}
    if(e.target.id==='rx-outcome'){filters.outcome=e.target.value;render();}
  });
  root.addEventListener('mouseover',function(e){
    var button=e.target.closest('.rx-result-cell'); if(!button||button.contains(e.relatedTarget)) return;
    clearTimeout(peekTimer);
    peekTimer=setTimeout(function(){
      var r=getRun(button.dataset.task,button.dataset.config), c=cfgById[button.dataset.config], rect=button.getBoundingClientRect();
      if(!peek) return;
      peek.innerHTML='<b>'+esc(configLabel(c))+'</b><span>'+esc(displayState(button.dataset.task,c.id))+' · '+esc(metricValue(r))+'</span>'
        +'<p>'+esc(r&&r.reason?(r.reason.split(/\n/)[0]):'No judge verdict recorded.')+'</p>';
      peek.style.left=Math.min(rect.left,window.innerWidth-286)+'px';
      peek.style.top=Math.min(rect.bottom+7,window.innerHeight-120)+'px';
      peek.hidden=false;
    },250);
  });
  root.addEventListener('mouseout',function(e){
    var button=e.target.closest('.rx-result-cell'); if(!button||button.contains(e.relatedTarget)) return;
    clearTimeout(peekTimer); if(peek) peek.hidden=true;
  });
  document.addEventListener('keydown',function(e){
    var target=e.target;
    if(target&&(/INPUT|TEXTAREA|SELECT/.test(target.tagName)||target.isContentEditable)) return;
    if(e.key==='Escape'&&selection){e.preventDefault();setSelection(null);return;}
    if(!selection) return;
    if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();moveTask(e.key==='ArrowUp'?-1:1);}
    else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();moveConfig(e.key==='ArrowLeft'?-1:1);}
    else if(e.key==='Enter'){pinned=!pinned;announce(pinned?'Detail panel pinned.':'Detail panel unpinned.');}
  });
  window.addEventListener('popstate',function(){selection=querySelection();render();});
  selection=querySelection();
  render();
})();
"""


# Model toggles: re-derive EVERY aggregate (hero, breakdowns, coverage, header) client-side for the visible
# subset, and show/hide the scatter dots + detail columns. Mirrors the Python renderers so a round-trip
# back to all-visible reproduces the server render. Runs only on toggle (the server already renders the
# default subset, so no load flash).
TOGGLE_JS = r"""
(function(){
  if (typeof Q_STATS === 'undefined') return;
  var SCORE={success:1,partial:.5,fail:0,error:0};
  var TOOLS=Q_STATS.tools, TLAB=Q_STATS.tlabels||{}, LAB=Q_STATS.labels, TASKS=Q_STATS.tasks, CELLS=Q_STATS.cells;
  var BASE=Q_STATS.baseline;   // the no-tool baseline — dropped from the per-model breakdowns only
  var hidden=new Set(Q_DEFHID||[]);
  var _d=document.createElement('div');
  function esc(s){ _d.textContent=(s==null?'':String(s)); return _d.innerHTML; }
  function lab(m){ return LAB[m]||m; }
  function ann(t){ return !!TASKS[t]; }
  function pct(p){ return p==null?'n/a':Math.round(p*100)+'%'; }
  function ft(s){ if(s==null)return 'n/a'; s=Math.round(s); return s<60?s+'s':Math.floor(s/60)+'m '+String(s%60).padStart(2,'0')+'s'; }
  function vis(){ return Q_MODELS.filter(function(m){return !hidden.has(m);}); }
  function where(f){ return CELLS.filter(f); }
  function visCells(){ return CELLS.filter(function(c){return !hidden.has(c[0]);}); }
  // 'error' = the judge API failed, not the agent. Excluded from the denominator (never scored as a 0.0
  // against the model); it stays counted in the verdict strip below so a broken judge run can't hide.
  var GRADED={success:1,partial:1,fail:1};
  function grade(cs){ var s=0,n=0; cs.forEach(function(c){ if(GRADED[c[3]]&&!ann(c[2])&&c[6]!=='stale'){s+=(SCORE[c[3]]||0);n++;} }); return n?[s/n,n]:[null,0]; }
  function atime(cs){ var s=0,n=0; cs.forEach(function(c){ if(c[3]==='success'&&!ann(c[2])&&c[4]!=null&&c[6]!=='stale'){s+=c[4];n++;} }); return n?[s/n,n]:[null,0]; }
  function nullLast(a,b){ return ((a[1]==null)-(b[1]==null)); }

  function mtCell(p,n,win,frac){ if(p==null&&n===0) return '<td class="mc empty">pending</td>';
    return '<td class="'+(win?'mc win':'mc')+'" style="--hm:'+(frac||0).toFixed(3)+'"><span class="mp">'+pct(p)+'</span><span class="mn">n='+n+'</span></td>'; }
  function ovcCell(p,n,extra){ return '<td class="mc ovc'+(extra||'')+'"><span class="mp">'+pct(p)+'</span><span class="mn">n='+n+'</span></td>'; }
  function logoOf(m){ return (typeof Q_LOGOS!=='undefined'&&Q_LOGOS[m])||''; }
  function famOf(m){ return (typeof Q_FAMILY!=='undefined'&&Q_FAMILY[m])||''; }
  // label-level split (mirrors base_of / think_of / eff_rank in report.py) — the scatter groups by label
  function baseOf(s){ var i=s.indexOf(' ('); return i<0?s:s.slice(0,i).trim(); }
  function thinkOf(s){ var a=s.indexOf('('), b=s.lastIndexOf(')'); return (a>=0&&b>a)?s.slice(a+1,b).trim():''; }
  var EFF={'no think':0,'no-think':0,'none':0,'low':1,'med':2,'medium':2,'high':3,'xhigh':4};
  function effRank(s){ var e=thinkOf(s).toLowerCase(); return e in EFF?EFF[e]:99; }
  function baseName(m){ return baseOf(lab(m)); }
  function thinkLevel(m){ return thinkOf(lab(m)); }
  function modelGroups(list){ var g=[],idx={}; list.forEach(function(m){ var b=baseName(m); if(!(b in idx)){idx[b]=g.length; g.push([b,[]]);} g[idx[b]][1].push(m); }); return g; }
  function hero(){
    var vm=vis(), hmax=0;   // top score across per-tool data cells -> heatmap scale (full colour at top)
    vm.forEach(function(m){ TOOLS.forEach(function(tl){ var g=grade(where(function(c){return c[0]===m&&c[1]===tl;}))[0]; if(g!=null&&g>hmax)hmax=g; }); });
    if(hmax<=0)hmax=1;
    var h='<div class="hlabel">completion score by model and tool</div><table class="mt"><thead><tr><th class="rh">model</th>';
    TOOLS.forEach(function(tl){ var tv=(typeof Q_TOOLVER!=='undefined'&&Q_TOOLVER[tl]); h+='<th>'+esc(TLAB[tl]||tl)+(tv?'<span class="tvh">v'+esc(tv)+'</span>':'')+'</th>'; }); h+='<th class="ov">overall</th></tr></thead><tbody>';
    function row(rowcls, family, labelHtml, pred){
      var cv={},rb=null;
      TOOLS.forEach(function(tl){ cv[tl]=grade(where(function(c){return pred(c)&&c[1]===tl;})); if(cv[tl][0]!=null&&(rb==null||cv[tl][0]>rb))rb=cv[tl][0]; });
      h+='<tr class="'+rowcls+'"'+(family?' data-family="'+family+'"':'')+'>'+labelHtml;
      TOOLS.forEach(function(tl){ var p=cv[tl][0]; h+=mtCell(p,cv[tl][1], p!=null&&rb!=null&&rb>0&&p===rb, p!=null?p/hmax:0); });
      var po=grade(where(pred)); h+=ovcCell(po[0],po[1])+'</tr>';
    }
    modelGroups(vis()).forEach(function(grp){
      var base=grp[0], members=grp[1], fam=famOf(members[0]);
      if(members.length===1){ var m=members[0];
        row('grp-parent', fam, '<th class="rh">'+logoOf(m)+esc(lab(m))+'</th>', function(c){return c[0]===m;}); return; }
      var mset={}; members.forEach(function(m){mset[m]=1;});
      row('grp-parent', fam, '<th class="rh">'+logoOf(members[0])+esc(base)+'</th>', function(c){return mset[c[0]];});
      members.forEach(function(m){ row('grp-child', fam, '<th class="rh sub">'+esc(thinkLevel(m))+'</th>', function(c){return c[0]===m;}); });
    });
    h+='</tbody><tfoot><tr><th class="rh ov">overall</th>'; var vc=visCells();
    TOOLS.forEach(function(tl){ var g=grade(vc.filter(function(c){return c[1]===tl;})); h+=ovcCell(g[0],g[1]); });
    var gg=grade(vc); return h+ovcCell(gg[0],gg[1],' grand')+'</tr></tfoot></table>';
  }
  // label cell of a horizontal bar row; i[3], when present, is a provider mark (trusted inline HTML)
  function blab(i){ return '<span class="blab">'+(i[3]||'')+'<span class="bname">'+esc(i[0])+'</span></span>'; }
  function pbar(title,items){
    items=items.slice().sort(function(a,b){ return nullLast(a,b)||((b[1]||0)-(a[1]||0)); });
    var vv=items.filter(function(i){return i[1]!=null;}).map(function(i){return i[1];}); var best=vv.length?Math.max.apply(null,vv):null;
    var o=items.map(function(i){ var f=i[1],n=i[2];
      if(f==null) return '<div class="brow">'+blab(i)+'<span class="btrack"></span><span class="bval na">n/a</span></div>';
      var w=best!=null&&best>0&&f===best;
      return '<div class="brow">'+blab(i)+'<span class="btrack"><span class="'+(w?'bfill win':'bfill')+'" style="width:'+Math.max(f*100,1.2).toFixed(1)+'%"></span></span><span class="'+(w?'bval win':'bval')+'">'+Math.round(f*100)+'<i>%</i><b>('+n+')</b></span></div>';
    }).join('');
    return '<section class="panel"><h3>'+esc(title)+'</h3><div class="bars">'+o+'</div></section>';
  }
  function pbarV(title,items){
    items=items.slice().sort(function(a,b){ return nullLast(a,b)||((b[1]||0)-(a[1]||0)); });
    var vv=items.filter(function(i){return i[1]!=null;}).map(function(i){return i[1];}); var best=vv.length?Math.max.apply(null,vv):null;
    var o=items.map(function(i){ var l=esc(i[0]),f=i[1],n=i[2];
      if(f==null) return '<div class="vcol"><span class="vval na">n/a</span><div class="vbar na"></div><span class="vlab">'+l+'</span></div>';
      var w=best&&f===best?' win':'';
      return '<div class="vcol" title="'+Math.round(f*100)+'% · '+n+' scored"><span class="vval">'+Math.round(f*100)+'%</span><div class="vbar'+w+'" style="height:'+Math.max(f*100,2).toFixed(1)+'%"></div><span class="vlab'+w+'">'+l+'</span></div>';
    }).join('');
    return '<section class="panel"><h3>'+esc(title)+'</h3><div class="vbars">'+o+'</div></section>';
  }
  function tbar(title,items){
    items=items.slice().sort(function(a,b){ return nullLast(a,b)||((a[1]||0)-(b[1]||0)); });
    var vv=items.filter(function(i){return i[1];}).map(function(i){return i[1];}); var mx=vv.length?Math.max.apply(null,vv):null, mn=vv.length?Math.min.apply(null,vv):null;
    var o=items.map(function(i){ var s=i[1],n=i[2];
      if(!s) return '<div class="brow">'+blab(i)+'<span class="btrack"></span><span class="bval na">n/a</span></div>';
      var w=mx?Math.max(s/mx*100,2):2;
      return '<div class="brow">'+blab(i)+'<span class="btrack"><span class="bfill'+(s===mn?' win':'')+'" style="width:'+w.toFixed(1)+'%"></span></span><span class="bval">'+ft(s)+'<i> n'+n+'</i></span></div>';
    }).join('');
    return '<section class="panel"><h3>'+esc(title)+'</h3><div class="bars">'+o+'</div></section>';
  }
  function tbarV(title,items){
    items=items.slice().sort(function(a,b){ return nullLast(a,b)||((a[1]||0)-(b[1]||0)); });
    var vv=items.filter(function(i){return i[1];}).map(function(i){return i[1];}); var mx=vv.length?Math.max.apply(null,vv):null, mn=vv.length?Math.min.apply(null,vv):null;
    var o=items.map(function(i){ var l=esc(i[0]),s=i[1],n=i[2];
      if(!s) return '<div class="vcol" title="no successful runs"><span class="vval na">n/a</span><div class="vbar na"></div><span class="vlab">'+l+'</span></div>';
      var hh=mx?Math.max(s/mx*100,2):2, w=s===mn?' win':'';
      return '<div class="vcol" title="'+ft(s)+' avg · '+n+' successful run(s)"><span class="vval">'+ft(s)+'</span><div class="vbar'+w+'" style="height:'+hh.toFixed(1)+'%"></div><span class="vlab'+w+'">'+l+'</span></div>';
    }).join('');
    return '<section class="panel"><h3>'+esc(title)+'</h3><div class="vbars">'+o+'</div></section>';
  }
  // mirrors report.py: the tool panels keep the no-tool baseline (it is the bar the tools are read
  // against), the model panels drop it (it is not a model comparand), and the titles drop the
  // "· by tool" / "· by model" the bar labels already state.
  function breakdowns(){
    var vc=visCells(), bc=vc.filter(function(c){return c[1]!==BASE;}), V=vis();
    var h='<div class="grid-2">';
    h+=pbarV('completion score', TOOLS.map(function(tl){ var g=grade(vc.filter(function(c){return c[1]===tl;})); return [TLAB[tl]||tl,g[0],g[1]]; }));
    h+=tbarV('avg time / task', TOOLS.map(function(tl){ var g=atime(vc.filter(function(c){return c[1]===tl;})); return [TLAB[tl]||tl,g[0],g[1]]; }));
    h+='</div><div class="grid-sm">';
    h+=pbar('completion score', V.map(function(m){ var g=grade(bc.filter(function(c){return c[0]===m;})); return [lab(m),g[0],g[1],logoOf(m)]; }));
    h+=tbar('avg time / task', V.map(function(m){ var g=atime(bc.filter(function(c){return c[0]===m;})); return [lab(m),g[0],g[1],logoOf(m)]; }));
    return h+'</div>';
  }
  function coverage(){
    var vc=visCells(), ov=grade(vc);
    var dag={judged:0,completed:0,stale:0,pending:0}; vc.forEach(function(c){ if(!ann(c[2])) dag[c[6]]=(dag[c[6]]||0)+1; });
    var vd={success:0,partial:0,fail:0,error:0}; vc.forEach(function(c){ if(c[3]&&!ann(c[2])&&c[6]!=='stale'&&vd[c[3]]!=null) vd[c[3]]++; });
    var h='<span class="ovr"><b>'+pct(ov[0])+'</b>completion</span><span class="sep"></span>';
    ['judged','completed','stale','pending'].forEach(function(k){ if(dag[k]) h+='<span class="vd" data-s="'+k+'"><i></i>'+k+'<b>'+dag[k]+'</b></span>'; });
    h+='<span class="sep"></span>';
    // 'error' shown only when non-zero — it means the JUDGE failed on that run (re-run judge.py to retry),
    // and it is NOT in the success-rate denominator, so it must stay visible rather than silently vanish.
    ['success','partial','fail','error'].forEach(function(k){ if(k==='error'&&!vd[k]) return;
      h+='<span class="vd" data-s="'+k+'"><i></i>'+k+'<b>'+vd[k]+'</b></span>'; });
    return h;
  }
  // cost-efficiency scatter — mirrors scatter_svg() in report.py. It re-renders for the visible set while
  // retaining an x-axis derived from every report model, so toggling cannot move or clip points.
  function fmtPrice(p){ if(p==null)return 'n/a'; if(p<=0)return 'free'; return '$'+p.toFixed(2); }
  function niceTicks(mx,n){ n=n||5; if(mx<=0)return[0,1];
    var raw=mx/n, mag=Math.pow(10,Math.floor(Math.log10(raw)));
    var step=[1,2,2.5,5,10].map(function(m){return m*mag;}).filter(function(v){return v>=raw;})[0];
    var ticks=[],t=0; while(t<mx){ ticks.push(Math.round(t*1e6)/1e6); t+=step; } ticks.push(Math.round(t*1e6)/1e6); return ticks; }
  var TMARK={'argent':'circle','agent-device':'square','none':'diamond'};   // shape encodes the tool
  var SCAT_AX_X=62, SCAT_AX_Y=424-52;   // ML, H-MB from scatter() — fixed regardless of data, crosshair endpoints
  function marker(shape,cx,cy,cls,attrs){
    attrs=attrs||'';
    if(shape==='square') return '<rect class="'+cls+'" '+attrs+' x="'+(cx-4.3).toFixed(1)+'" y="'+(cy-4.3).toFixed(1)+'" width="8.6" height="8.6" rx="1"></rect>';
    if(shape==='diamond') return '<path class="'+cls+'" '+attrs+' d="M'+cx.toFixed(1)+' '+(cy-6.2).toFixed(1)+'L'+(cx+6.2).toFixed(1)+' '+cy.toFixed(1)+'L'+cx.toFixed(1)+' '+(cy+6.2).toFixed(1)+'L'+(cx-6.2).toFixed(1)+' '+cy.toFixed(1)+'Z"></path>';
    return '<circle class="'+cls+'" '+attrs+' cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="5"></circle>';
  }
  function scatterPts(modelList){   // one point per (model, tool) — tools are NOT collapsed into one dot
    var out=[];
    (modelList||vis()).forEach(function(m){ TOOLS.forEach(function(tl){
      var cs=where(function(c){return c[0]===m&&c[1]===tl;}), g=grade(cs);
      var sum=0,k=0; cs.forEach(function(c){ if(c[3]&&!ann(c[2])&&c[5]!=null&&c[6]!=='stale'){sum+=c[5];k++;} });
      if(!k||g[0]==null) return;
      out.push({lab:lab(m), tool:tl, pr:sum/k, sc:g[0], n:g[1], fam:famOf(m)});
    }); });
    return out;
  }
  // first-appearance index of each family's base models over the given points -> {'fam|base': 0|1|2}.
  // v0 (index 0, no class) is the family's own hue; v1/v2 step lightness within the family so a second
  // or third model sharing a family stays distinguishable once toggled into view together. A fourth+
  // model in one family reuses v2 (the roster maxes at two per family).
  function famVariants(pts){
    var order={}, out={};
    pts.forEach(function(p){ if(!p.fam) return;
      var b=baseOf(p.lab), lst=order[p.fam]||(order[p.fam]=[]);
      if(lst.indexOf(b)<0) lst.push(b); });
    Object.keys(order).forEach(function(fam){ order[fam].forEach(function(b,i){ out[fam+'|'+b]=Math.min(i,2); }); });
    return out;
  }
  function famCls(fam,base,variants){   // CSS class carrying a dot/connector's colour, or '' with no family
    if(!fam) return '';
    var v=variants[fam+'|'+base]||0;
    return 'fam-'+fam+(v?' v'+v:'');
  }
  var activeModel=null, activeTool=null;
  function scatterSelectionActive(){ return !!activeModel||!!activeTool; }
  function scatterSelectionMatches(model,tool){ return (!activeModel||model===activeModel)&&(!activeTool||tool===activeTool); }
  function pairAttrs(model,tool){ return 'data-pair="'+esc(model+'|'+tool)+'" data-model="'+esc(model)+'" data-tool="'+esc(tool)+'"'; }
  function selectAttrs(model,tool,tip){ return pairAttrs(model,tool)+' data-scatter-tip="'+esc(tip)+'" role="button" tabindex="0" aria-pressed="false" aria-label="'+esc('Select '+model+' × '+(TLAB[tool]||tool))+'"'; }
  // Connectors + markers share a selectable base-model × tool identity.
  function scatterLayer(dots,variants){
    var out='', groups={}, order=[];
    dots.forEach(function(d){ var k=baseOf(d.lab)+'|'+d.tool;
      if(!groups[k]){ groups[k]=[]; order.push(k); } groups[k].push(d); });
    order.forEach(function(k){ var mem=groups[k]; if(mem.length<2) return;   // a lone dot gets no connector
      var ord=mem.slice().sort(function(a,b){return effRank(a.lab)-effRank(b.lab)||a.cx-b.cx;});
      var fam=ord[0].fam, base=baseOf(ord[0].lab), tool=ord[0].tool;
      var cls=famCls(fam,base,variants);
      var path=ord.map(function(m){return m.cx.toFixed(1)+','+m.cy.toFixed(1);}).join(' ');
      out+='<polyline class="ptline'+(cls?' '+cls:'')+'" '+pairAttrs(base,tool)+' aria-hidden="true" points="'+path+'"/>'
        +'<polyline class="ptline-hit" '+selectAttrs(base,tool,base+' × '+(TLAB[tool]||tool))+' points="'+path+'"/>'; });
    dots.forEach(function(d){
      var tip=(thinkOf(d.lab)||'no think')+' — '+fmtPrice(d.pr)+' · '+pct(d.sc);
      var base=baseOf(d.lab), cls=famCls(d.fam,base,variants);
      var attrs=selectAttrs(base,d.tool,tip)+' data-cx="'+d.cx.toFixed(1)+'" data-cy="'+d.cy.toFixed(1)+'"';
      out+=marker(TMARK[d.tool]||'circle', d.cx, d.cy, cls?'pt '+cls:'pt', attrs); });
    return out;
  }
  function scatterLegend(dots){
    var modelBases=[];
    dots.forEach(function(d){ var base=baseOf(d.lab); if(modelBases.indexOf(base)<0) modelBases.push(base); });
    var famOfBase={};
    dots.forEach(function(d){ var b=baseOf(d.lab); if(!famOfBase[b]) famOfBase[b]=d.fam||'none'; });
    var models=modelBases.map(function(base){
      return '<button type="button" class="scatterlegend-item" data-legend-model="'+esc(base)+'" aria-pressed="false">'
        +'<i class="legend-swatch fam-'+esc(famOfBase[base]||'none')+'"></i>'+esc(base)+'</button>'; }).join('');
    var tools=TOOLS.filter(function(tl){ return dots.some(function(d){return d.tool===tl;}); }).map(function(tl){
      return '<button type="button" class="scatterlegend-item" data-legend-tool="'+esc(tl)+'" aria-pressed="false">'+esc(TLAB[tl]||tl)+'</button>'; }).join('');
    return '<div class="scatterlegend" aria-label="Scatterplot legend">'
      +'<div class="scatterlegend-group"><span class="scatterlegend-title">model</span><div class="scatterlegend-items">'+models+'</div></div>'
      +'<div class="scatterlegend-group"><span class="scatterlegend-title">tool</span><div class="scatterlegend-items">'+tools+'</div></div></div>';
  }
  function scatter(){
    var pts=scatterPts();
    if(activeModel&&!pts.some(function(p){return baseOf(p.lab)===activeModel;})) activeModel=null;
    if(activeTool&&!pts.some(function(p){return p.tool===activeTool;})) activeTool=null;
    var scalePts=scatterPts(Q_MODELS);
    var W=680,H=424,ML=62,MR=26,MT=18,MB=52, pw=W-ML-MR, ph=H-MT-MB;
    var maxp=scalePts.reduce(function(a,p){return p.pr>a?p.pr:a;},0);
    var xticks=niceTicks(maxp*1.1), axmax=xticks[xticks.length-1]||1;
    function X(p){return ML+(p/axmax)*pw;} function Y(sv){return MT+(1-sv)*ph;}
    var dots=pts.map(function(d){ return {lab:d.lab,tool:d.tool,pr:d.pr,sc:d.sc,n:d.n,fam:d.fam,
                                          cx:X(d.pr),cy:Y(d.sc)}; });
    var variants=famVariants(pts);

    var ax='';   // gridlines, ticks, axis lines/titles
    [0,.25,.5,.75,1].forEach(function(yv){ var y=Y(yv);
      ax+='<line class="sg" x1="'+ML+'" y1="'+y.toFixed(1)+'" x2="'+(W-MR)+'" y2="'+y.toFixed(1)+'"/>';
      ax+='<text class="stk" x="'+(ML-9)+'" y="'+(y+3.6).toFixed(1)+'" text-anchor="end">'+Math.round(yv*100)+'</text>'; });
    xticks.forEach(function(xv){ var x=X(xv);
      ax+='<line class="sg" x1="'+x.toFixed(1)+'" y1="'+MT+'" x2="'+x.toFixed(1)+'" y2="'+(MT+ph)+'"/>';
      ax+='<text class="stk" x="'+x.toFixed(1)+'" y="'+(MT+ph+18).toFixed(1)+'" text-anchor="middle">'+esc(fmtPrice(xv))+'</text>'; });
    ax+='<line class="sax-l" x1="'+ML+'" y1="'+MT+'" x2="'+ML+'" y2="'+(MT+ph)+'"/>';
    ax+='<line class="sax-l" x1="'+ML+'" y1="'+(MT+ph)+'" x2="'+(W-MR)+'" y2="'+(MT+ph)+'"/>';
    ax+='<text class="sax" x="'+Math.round(ML+pw/2)+'" y="'+(H-9)+'" text-anchor="middle">avg price per task</text>';
    ax+='<text class="sax" transform="translate(15,'+Math.round(MT+ph/2)+') rotate(-90)" text-anchor="middle">completion score (%)</text>';

    var mainLayer=scatterLayer(dots,variants);

    return scatterLegend(dots)+'<section class="panel"><div class="scatterwrap"><svg viewBox="0 0 '+W+' '+H+'" class="scatter" preserveAspectRatio="xMidYMid meet" role="group" aria-label="price per task vs completion score by model and tool">'
      +ax+mainLayer+'</svg></div></section>';
  }
  function set(id,html){ var e=document.getElementById(id); if(e) e.innerHTML=html; }
  function applyScatterSelection(){
    var root=document.getElementById('stat-scatter'); if(!root) return;
    root.querySelectorAll('[data-pair]').forEach(function(el){
      var active=scatterSelectionActive()&&scatterSelectionMatches(el.getAttribute('data-model'),el.getAttribute('data-tool'));
      el.classList.toggle('is-muted', scatterSelectionActive()&&!active);
      if(el.getAttribute('role')==='button') el.setAttribute('aria-pressed', active?'true':'false');
    });
    root.querySelectorAll('[data-legend-model]').forEach(function(el){
      el.setAttribute('aria-pressed', el.getAttribute('data-legend-model')===activeModel?'true':'false'); });
    root.querySelectorAll('[data-legend-tool]').forEach(function(el){
      el.setAttribute('aria-pressed', el.getAttribute('data-legend-tool')===activeTool?'true':'false'); });
  }
  function selectScatterTarget(target){
    var el=target.closest&&target.closest('[data-pair]');
    if(el){ var model=el.getAttribute('data-model'), tool=el.getAttribute('data-tool');
      if(activeModel===model&&activeTool===tool){ activeModel=null; activeTool=null; }
      else{ activeModel=model; activeTool=tool; }
      applyScatterSelection(); return;
    }
    var modelEl=target.closest&&target.closest('[data-legend-model]');
    if(modelEl){ var nextModel=modelEl.getAttribute('data-legend-model'); activeModel=activeModel===nextModel?null:nextModel;
      applyScatterSelection(); return; }
    var toolEl=target.closest&&target.closest('[data-legend-tool]');
    if(toolEl){ var nextTool=toolEl.getAttribute('data-legend-tool'); activeTool=activeTool===nextTool?null:nextTool;
      applyScatterSelection(); return; }
    activeModel=null; activeTool=null;
    applyScatterSelection();
  }
  var scatterRoot=document.getElementById('stat-scatter');
  var scatterTip=null, scatterTipTarget=null;
  var crosshairX=null, crosshairY=null, crosshairSvg=null;
  function hideCrosshair(){ if(crosshairX) crosshairX.remove(); if(crosshairY) crosshairY.remove(); crosshairX=crosshairY=crosshairSvg=null; }
  function showCrosshair(svg,cx,cy,color){
    if(crosshairSvg!==svg){
      hideCrosshair(); crosshairSvg=svg;
      crosshairX=document.createElementNS('http://www.w3.org/2000/svg','line');
      crosshairY=document.createElementNS('http://www.w3.org/2000/svg','line');
      crosshairX.setAttribute('class','scatter-crosshair'); crosshairX.setAttribute('aria-hidden','true');
      crosshairY.setAttribute('class','scatter-crosshair'); crosshairY.setAttribute('aria-hidden','true');
      svg.appendChild(crosshairX); svg.appendChild(crosshairY);
    }
    crosshairX.setAttribute('x1',cx); crosshairX.setAttribute('x2',cx);
    crosshairX.setAttribute('y1',cy); crosshairX.setAttribute('y2',SCAT_AX_Y);
    crosshairY.setAttribute('x1',SCAT_AX_X); crosshairY.setAttribute('x2',cx);
    crosshairY.setAttribute('y1',cy); crosshairY.setAttribute('y2',cy);
    crosshairX.style.stroke=color; crosshairY.style.stroke=color;
  }
  function hideScatterTip(){ scatterTipTarget=null; if(scatterTip) scatterTip.hidden=true; hideCrosshair(); }
  if(scatterRoot){
    scatterTip=document.createElement('div');
    scatterTip.className='scattertip'; scatterTip.hidden=true; scatterTip.setAttribute('role','tooltip');
    document.body.appendChild(scatterTip);
    function placeScatterTip(x,y){
      var pad=10,gap=12,w=scatterTip.offsetWidth,h=scatterTip.offsetHeight;
      var left=Math.min(window.innerWidth-w-pad,x+gap), top=y-h-gap;
      if(top<pad) top=Math.min(window.innerHeight-h-pad,y+gap);
      scatterTip.style.left=Math.max(pad,left)+'px'; scatterTip.style.top=Math.max(pad,top)+'px';
    }
    function showScatterTip(target,x,y){
      var tip=target&&target.getAttribute('data-scatter-tip'); if(!tip) return;
      scatterTipTarget=target; scatterTip.textContent=tip; scatterTip.hidden=false;
      if(x==null){ var rect=target.getBoundingClientRect(); x=rect.left+rect.width/2; y=rect.top; }
      placeScatterTip(x,y);
      var cxAttr=target.getAttribute('data-cx'), cyAttr=target.getAttribute('data-cy');
      if(cxAttr!=null&&cyAttr!=null){
        var svg=target.closest('svg.scatter');
        if(svg) showCrosshair(svg,parseFloat(cxAttr),parseFloat(cyAttr),getComputedStyle(target).fill);
      } else hideCrosshair();
    }
    scatterRoot.addEventListener('pointerover',function(e){
      var target=e.target.closest&&e.target.closest('[data-scatter-tip]');
      if(target) showScatterTip(target,e.clientX,e.clientY);
    });
    scatterRoot.addEventListener('pointermove',function(e){
      var target=e.target.closest&&e.target.closest('[data-scatter-tip]');
      if(target) showScatterTip(target,e.clientX,e.clientY);
    });
    scatterRoot.addEventListener('pointerout',function(e){
      var next=e.relatedTarget&&e.relatedTarget.closest&&e.relatedTarget.closest('[data-scatter-tip]');
      if(next!==scatterTipTarget) hideScatterTip();
    });
    scatterRoot.addEventListener('focusin',function(e){
      var target=e.target.closest&&e.target.closest('[data-scatter-tip]');
      if(target) showScatterTip(target);
    });
    scatterRoot.addEventListener('focusout',hideScatterTip);
    scatterRoot.addEventListener('click', function(e){ selectScatterTarget(e.target); });
    scatterRoot.addEventListener('keydown', function(e){
      if((e.key==='Enter'||e.key===' ')&&e.target.closest&&
         e.target.closest('[data-pair],[data-legend-model],[data-legend-tool]')){
        e.preventDefault(); selectScatterTarget(e.target);
      }
    });
  }
  function render(){
    hideScatterTip();
    set('stat-hero',hero()); set('stat-breakdowns',breakdowns()); set('stat-cov',coverage());
    set('stat-scatter',scatter()); applyScatterSelection();
    var V=vis(), vc=visCells();
    var nsc=vc.filter(function(c){return c[3]&&!ann(c[2])&&c[6]!=='stale';}).length;
    var nt=Object.keys(TASKS).filter(function(t){return !ann(t);}).length;
    var hm=document.getElementById('hdr-models'); if(hm) hm.textContent=V.length;
    var hs=document.getElementById('hdr-scored'); if(hs) hs.textContent=nsc+' / '+(V.length*TOOLS.length*nt);
    var best=null; V.forEach(function(m){ TOOLS.forEach(function(tl){ var g=grade(where(function(c){return c[0]===m&&c[1]===tl;})); if(g[0]!=null&&(best==null||g[0]>best[2])) best=[m,tl,g[0]]; }); });
    var hb=document.getElementById('hdr-best'); if(hb) hb.innerHTML=best?' · best <b>'+esc(lab(best[0]))+' / '+esc(best[1])+' '+pct(best[2])+'</b>':'';
    // detail grid: show/hide default-hidden columns (the scatter is fully re-rendered above, no per-dot toggle)
    Q_MODELS.forEach(function(m){ var hide=hidden.has(m);
      document.querySelectorAll('#detail-table [data-model="'+m+'"]').forEach(function(el){ el.classList.toggle('mhidden',hide); }); });
  }
  document.querySelectorAll('.mtcb').forEach(function(cb){
    cb.addEventListener('change', function(){
      var m=cb.getAttribute('data-model');
      if(cb.checked) hidden.delete(m); else hidden.add(m);
      var w=cb.closest('.mtog'); if(w) w.classList.toggle('off', !cb.checked);
      render();
    });
  });
})();
"""


# Tweaks menu — the gear button opens a panel whose controls each drive a CSS var / data-attribute on
# <html> and persist to localStorage. Family colours only override when set (else follow --accent).
TWEAKS_JS = r"""
(function(){
  var root=document.documentElement, panel=document.getElementById('tw-panel'), btn=document.getElementById('tw-btn');
  if(!panel||!btn) return;
  // v5: the published report uses one shape system: 16px panels, pill controls, and hairline borders.
  // Bumping the key retires stale v4 preferences so returning visitors pick up the new defaults.
  // new defaults.
  // the stack and the heatmap hue both live in theme.css — Python and JS only name the token
  /* v6: --heat-classic no longer exists, so a v5 blob would restore a dangling var() and leave the
     heatmap with no ground at all. Bumping the key retires those blobs instead of migrating them. */
  var KEY='acb-tweaks-v6', DEFFONT='var(--grotesque)', DEFHEAT='var(--heat-ink)';
  function $(id){ return document.getElementById(id); }
  // The brand hues have exactly ONE definition: --fam-* in theme.css. The colour pickers seed their
  // swatches by reading them back out (they used to carry a hardcoded value= copied from a Python dict).
  // Snapshot BEFORE applyFam() writes inline overrides onto <html>, or we'd read our own value back.
  var FAMDEF={}, cs=getComputedStyle(root);
  document.querySelectorAll('.tw-fam').forEach(function(c){
    var v=cs.getPropertyValue('--fam-'+c.dataset.fam).trim();
    FAMDEF[c.dataset.fam]=v; c.value=v;
  });
  function famDefault(f){ return FAMDEF[f]||''; }
  // same trick for the heat exponent: the default lives in theme.css (--tw-heat-exp), and the number
  // input is seeded from it, so the value is not restated here. Read before applyBase() writes it back.
  var DEFEXP=cs.getPropertyValue('--tw-heat-exp').trim();
  $('tw-heat-exp').value=DEFEXP;
  btn.addEventListener('click', function(e){ e.stopPropagation(); panel.hidden=!panel.hidden; });
  document.addEventListener('click', function(e){ if(!panel.hidden && !panel.contains(e.target) && e.target!==btn) panel.hidden=true; });

  function rad(numId,unitId){ return (parseFloat($(numId).value)||0)+(($(unitId)&&$(unitId).value)||'px'); }
  function applyBase(){
    root.setAttribute('data-theme', $('tw-dark').checked?'dark':'');
    root.setAttribute('data-tw-heatmap', $('tw-heat').checked?'on':'off');
    // 'exp' selects the exponential ramp in report.css; anything else leaves the linear one standing
    root.setAttribute('data-tw-heat-curve', $('tw-heat-curve').checked?'exp':'linear');
    // on -> section methodology captions render inline; off (default) -> they live in the [i] tooltip
    root.setAttribute('data-tw-explain', $('tw-explain').checked?'on':'off');
    root.style.setProperty('--heat', $('tw-heat-hue').value||DEFHEAT);
    root.style.setProperty('--tw-heat-exp', $('tw-heat-exp').value||DEFEXP);
    root.style.setProperty('--tw-shadow', $('tw-shadow').checked?'var(--tw-shadow-on)':'none');
    root.style.setProperty('--tw-bw', $('tw-border').checked?'var(--tw-bw-on)':'0px');
    root.style.setProperty('--tw-radius', rad('tw-radius','tw-radius-unit'));
    root.style.setProperty('--tw-pill-radius', rad('tw-pill','tw-pill-unit'));
    root.style.setProperty('--tw-font', $('tw-font').value||DEFFONT);
    root.style.setProperty('--tw-parent', $('tw-parent').value||'1');
  }
  function applyFam(){ document.querySelectorAll('.tw-fam').forEach(function(c){
    root.style.setProperty('--fam-'+c.dataset.fam, c.value); }); }   // pickers default to brand hues
  function save(){
    var fam={}; document.querySelectorAll('.tw-fam').forEach(function(c){ fam[c.dataset.fam]=c.value; });
    var s={dark:$('tw-dark').checked,heat:$('tw-heat').checked,heathue:$('tw-heat-hue').value,
      heatcurve:$('tw-heat-curve').checked,heatexp:$('tw-heat-exp').value,
      shadow:$('tw-shadow').checked,border:$('tw-border').checked,explain:$('tw-explain').checked,
      radius:$('tw-radius').value,runit:$('tw-radius-unit').value,pill:$('tw-pill').value,punit:$('tw-pill-unit').value,
      font:$('tw-font').value,parent:$('tw-parent').value,fam:fam};
    try{ localStorage.setItem(KEY, JSON.stringify(s)); }catch(_){}
  }
  function restore(){
    var s=null; try{ s=JSON.parse(localStorage.getItem(KEY)); }catch(_){}
    if(s){
      $('tw-dark').checked=!!s.dark; $('tw-heat').checked=!!s.heat;
      // every v4 blob carries all fields; the null-guards are only defence against a hand-edited blob
      if(s.heathue!=null)$('tw-heat-hue').value=s.heathue;
      $('tw-heat-curve').checked=!!s.heatcurve;
      if(s.heatexp!=null&&s.heatexp!=='')$('tw-heat-exp').value=s.heatexp;
      $('tw-shadow').checked=!!s.shadow; $('tw-border').checked=!!s.border;
      $('tw-explain').checked=!!s.explain;   // absent in pre-explanations blobs -> false = hidden (default)
      if(s.radius!=null)$('tw-radius').value=s.radius; if(s.runit)$('tw-radius-unit').value=s.runit;
      if(s.pill!=null)$('tw-pill').value=s.pill; if(s.punit)$('tw-pill-unit').value=s.punit;
      if(s.font!=null)$('tw-font').value=s.font; if(s.parent!=null)$('tw-parent').value=s.parent;
      if(s.fam) document.querySelectorAll('.tw-fam').forEach(function(c){ if(s.fam[c.dataset.fam])c.value=s.fam[c.dataset.fam]; });
    }
    applyBase(); applyFam();
  }
  ['tw-dark','tw-heat','tw-heat-hue','tw-heat-curve','tw-heat-exp','tw-shadow','tw-border','tw-explain','tw-radius','tw-radius-unit','tw-pill','tw-pill-unit','tw-font','tw-parent'].forEach(function(id){
    var el=$(id); if(!el)return; ['input','change'].forEach(function(ev){ el.addEventListener(ev, function(){ applyBase(); save(); }); });
  });
  document.querySelectorAll('.tw-fam').forEach(function(c){
    ['input','change'].forEach(function(ev){ c.addEventListener(ev, function(){ applyFam(); save(); }); });
  });
  $('tw-reset').addEventListener('click', function(){
    try{ localStorage.removeItem(KEY); }catch(_){}
    $('tw-dark').checked=false; $('tw-heat').checked=true; $('tw-heat-hue').value=DEFHEAT;
    $('tw-heat-curve').checked=true; $('tw-heat-exp').value=DEFEXP;
    $('tw-shadow').checked=false; $('tw-border').checked=true; $('tw-explain').checked=false;
    $('tw-radius').value=16; $('tw-radius-unit').value='px'; $('tw-pill').value=50; $('tw-pill-unit').value='%';
    $('tw-font').value=DEFFONT; $('tw-parent').value=1;
    document.querySelectorAll('.tw-fam').forEach(function(c){
      root.style.removeProperty('--fam-'+c.dataset.fam);          // fall back to theme.css
      c.value=famDefault(c.dataset.fam);
    });
    applyBase();
  });
  restore();
})();
"""


def shown_models(rows):
    """Models to render: internal ones are dropped in public mode, and also whenever the dataset carries
    no result rows for them — so the public branch (which ships no internal dirs) never surfaces them,
    flag or not. Non-internal models always show (pending ones render as empty columns, as before)."""
    def ok(m):
        if not is_internal(m):
            return True
        if PUBLIC:
            return False
        return any(k[0] == m for k in rows)
    return [m for m in MODELS if ok(m)]


def build(tasks, tmap, rows, models, title, artifacts_rel, now,
          report_name="benchmark-report.html", explorer_name="benchmark-report-runs.html"):
    """Builds two pages sharing one data pass: the report (hero/leaderboard/matrix/breakdowns/efficiency/
    methodology) and the run explorer (per-task detail grid + run-detail modal), linked via
    the header nav. Returns (report_html, explorer_html)."""
    apps = sorted(set(t["app"] for t in tasks))
    def tof(r, key):
        t = tmap.get(r["meta"].get("task"))
        return (t or {}).get(key) or r["meta"].get(key)
    appof = lambda r: tof(r, "app")
    kindof = lambda r: tof(r, "kind")

    # annulled tasks (tasks.json) — excluded from every score/rate/coverage count, rendered 'annulled'.
    ANNULLED.clear()
    ANNULLED.update(t["id"] for t in tasks if t.get("annulled"))
    # `models`/`rows` arrive filtered by main() (shown_models). The page adds on-page toggles to hide/show
    # any model from every stat; DEFAULT_HIDDEN starts some unchecked. `vis`/`rows_vis` = the initially-shown
    # subset, driving every AGGREGATE stat below so the default view already excludes them (no load flash).
    # The full `models`/`cells` still build the toggle bar + detail grid (hidden columns are CSS-hidden and
    # revealed by toggling). On toggle, TOGGLE_JS re-derives all of this client-side from the embedded Q_CELLS.
    cells = [(m, tl) for m in models for tl in TOOLS]
    vis = [m for m in models if not default_hidden(m)]
    _vset = set(vis)
    rows_vis = {k: r for k, r in rows.items() if k[0] in _vset}

    # DAG state per unit, derived from disk: pending -> completed (ran, awaiting judge) -> judged (verdict).
    def unit_state(m, tl, tid):
        r = rows.get((m, tl, tid))
        if not r or r["meta"].get("returncode") == -2:
            return "pending"
        if r.get("polluted"):     # result from an older/polluted harness — designated for rerun, doesn't count
            return "stale"
        if r["verdict"] in ("success", "partial", "fail"):
            return "judged"
        if r["png"]:
            return "completed"
        return "pending"

    n_scored = sum(1 for k, r in rows_vis.items() if r["verdict"] and not r.get("polluted") and k[2] not in ANNULLED)
    overall, _ = grade(rows_vis, lambda k, r: True)
    dist_all = vdist(rows_vis, lambda k, r: True)

    leaders = []
    for m, tl in [(mm, tl) for mm in vis for tl in TOOLS]:
        completion, n = grade(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl)
        avg_s, _ = avg_time_success(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl)
        price, _ = avg_price(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl)
        if completion is not None:
            leaders.append((m, tl, completion, avg_s, price, n))
    leaders.sort(key=lambda x: (-x[2], x[3] is None, x[3] or 0))
    best = leaders[0] if leaders else None
    # Hero atlas: the best config's completion score, rendered as a dithered dot lattice.
    atlas_number = round(best[2] * 100) if best else 0
    graded_task_sets = [
        {k[2] for k, r in rows.items()
         if k[0] == m and k[1] == tl and r["verdict"] in GRADED
         and not r.get("polluted") and k[2] not in ANNULLED}
        for m in vis for tl in TOOLS
    ]
    shared_tasks = set.intersection(*graded_task_sets) if graded_task_sets else set()

    # Three real runs anchor the methodology explanation: one success, one partial, one failure. They
    # use the same rows/tasks/artifact path as the rest of the report, so the examples cannot drift away
    # from the published evidence. A partial dataset simply renders whichever examples it contains.
    method_specs = [
        (("gpt_high", "argent", "element-28"), "Compose", "Send “status update”"),
        (("gpt_high", "argent", "element-22"), "Search", "Search for “update”"),
        (("gpt_high", "argent", "bsky-25"), "Reply", "Reply “nice one”"),
    ]

    def method_time(seconds):
        seconds = int(round(seconds or 0))
        return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"

    method_examples = []
    for (m, tl, tid), activity, short in method_specs:
        r, task = rows.get((m, tl, tid)), tmap.get(tid)
        if not r or not task or not r.get("png") or r.get("verdict") not in GRADED:
            continue
        meta, verdict = r.get("meta") or {}, r["verdict"]
        confidence = r.get("conf")
        method_examples.append({
            "id": tid, "app": (task.get("app") or "").title(), "activity": activity, "short": short,
            "model": mlabel(m),
            "verdict": verdict, "score": f"{SCORE[verdict]:.1f}",
            "confidence": f"{confidence:.2f}".lstrip("0") if confidence is not None else "n/a",
            "time": method_time(meta.get("wall_s")), "tool": tl_label(tl),
            "prompt": task.get("prompt") or "",
            "reason": r.get("reason") or "", "shot": f"{artifacts_rel}/{m}__{tl}__{tid}.png",
            "alt": f"Final screenshot for {tid}, graded {verdict}",
        })

    def doc_head(page_title, body_class=""):
        body_attr = f' class="{body_class}"' if body_class else ""
        return [f'<!doctype html><html lang="en" data-tw-heatmap="on" data-tw-heat-curve="exp"><head><meta charset="utf-8">',
                f'<meta name="viewport" content="width=device-width,initial-scale=1">',
                f'<title>{html.escape(page_title)}</title><style>{CSS}</style></head><body{body_attr}><div class="wrap">']

    P = doc_head(title)

    # tweaks menu — a live look-and-feel playground (gear button, top-right). Every control drives a CSS
    # var / data-attribute on <html> via TWEAKS_JS and persists to localStorage.
    FAM_NAMES = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "meta": "Meta",
                 "xai": "xAI", "mistral": "Mistral", "deepseek": "DeepSeek", "qwen": "Qwen"}
    fams_present = []
    for m in models:
        f = provider_of(m)
        if f and f not in fams_present:
            fams_present.append(f)
    fam_pickers = "".join(
        f'<div class="tw-row"><span>{FAM_NAMES.get(f, f)}</span>'
        f'<input type="color" class="tw-fam" data-fam="{f}"></div>'
        for f in fams_present)
    # every stack is a theme.css token, so no font string lives in Python
    font_opts = [("var(--grotesque)", "Grotesque (default)"), ("var(--sans)", "System"),
                 ("var(--serif)", "Serif"), ("var(--mono)", "Monospace"),
                 ("var(--humanist)", "Humanist")]
    font_sel = "".join(f'<option value="{html.escape(v, quote=True)}">{lbl}</option>' for v, lbl in font_opts)
    # the heatmap's hue is a token too (see --heat in theme.css) — first option is the default
    heat_opts = [("var(--heat-ink)", "ink (default)"), ("var(--heat-navy)", "navy")]
    heat_sel = "".join(f'<option value="{html.escape(v, quote=True)}">{lbl}</option>' for v, lbl in heat_opts)

    def tw_toggle(tid, checked=False):
        c = " checked" if checked else ""
        return (f'<label class="tw-toggle"><input type="checkbox" class="tw-cb" id="{tid}"{c}>'
                f'<span class="tw-sw"></span></label>')
    # built once, appended to both pages — the tweaks panel is shared header chrome
    tw_panel_html = (
        '<div id="tw-panel" class="tw-panel" hidden>'
        '<div class="tw-head">tweaks<button class="tw-reset" id="tw-reset">reset</button></div>'
        f'<div class="tw-row"><span>dark mode</span>{tw_toggle("tw-dark", False)}</div>'
        f'<div class="tw-row"><span>heatmap</span>{tw_toggle("tw-heat", True)}</div>'
        f'<div class="tw-row"><span>heatmap colour</span><select id="tw-heat-hue">{heat_sel}</select></div>'
        # exponential ramp + its exponent. No value= on the number input: TWEAKS_JS seeds it from
        # --tw-heat-exp, so the default stays in theme.css alone (as with the family colour pickers).
        f'<div class="tw-row"><span>exponential heat</span>{tw_toggle("tw-heat-curve", True)}</div>'
        '<div class="tw-row"><span>heat exponent</span>'
        '<input type="number" id="tw-heat-exp" min="1" max="12" step="0.5"></div>'
        f'<div class="tw-row"><span>shadows</span>{tw_toggle("tw-shadow", False)}</div>'
        f'<div class="tw-row"><span>borders</span>{tw_toggle("tw-border", True)}</div>'
        # off by default: section methodology captions collapse to an [i] tooltip; this shows them inline
        f'<div class="tw-row"><span>explanations</span>{tw_toggle("tw-explain", False)}</div>'
        '<div class="tw-row"><span>corner radius</span><span class="tw-radius-wrap">'
        '<input type="number" id="tw-radius" min="0" max="32" step="1" value="16">'
        '<select id="tw-radius-unit"><option value="px">px</option><option value="%">%</option></select></span></div>'
        '<div class="tw-row"><span>pills</span><span class="tw-radius-wrap">'
        '<input type="number" id="tw-pill" min="0" max="99" step="1" value="50">'
        '<select id="tw-pill-unit"><option value="%">%</option><option value="px">px</option></select></span></div>'
        f'<div class="tw-row"><span>font</span><select id="tw-font">{font_sel}</select></div>'
        '<div class="tw-row"><span>parent height ×</span>'
        '<input type="number" id="tw-parent" min="0.5" max="4" step="0.25" value="1"></div>'
        + ('<div class="tw-sub">family colours</div>' + fam_pickers if fam_pickers else '')
        + '</div>')
    P.append(tw_panel_html)

    # Public chrome and benchmark provenance.
    tools_ver_str = " · ".join(f"{t} v{html.escape(TOOL_VERSIONS[t])}" for t in TOOLS if TOOL_VERSIONS.get(t))

    def _app_ver_bit(app, info):   # "bluesky v1.122.0 (7f5dd40)" with the commit linking to GitHub
        s = html.escape(app)
        if info.get("version"):
            s += f" v{html.escape(info['version'])}"
        if info.get("commit"):
            short, repo = html.escape(info["commit"][:7]), info.get("repo")
            link = (f'<a href="https://github.com/{html.escape(repo)}/commit/{html.escape(info["commit"])}"'
                    f' target="_blank" rel="noopener">{short}</a>') if repo else short
            s += f" ({link})"
        return s
    # only the surfaces THIS report covers — the manifest also pins apps that are frozen but not yet run
    # (they show up here on their own once results for them land).
    app_ver_str = ", ".join(_app_ver_bit(a, APP_VERSIONS[a]) for a in apps
                            if a in APP_VERSIONS
                            and (APP_VERSIONS[a].get("version") or APP_VERSIONS[a].get("commit")))

    # the judge, read off the verdicts on disk (score.json) rather than hardcoded, so it cannot go stale
    # when judge.py is repointed. Falls back to judge.JUDGE_MODEL before anything has been scored.
    judges = sorted({r["judge"] for k, r in rows_vis.items()
                     if r.get("judge") and r["verdict"] and k[2] not in ANNULLED})
    judge_str = " / ".join(html.escape(j) for j in judges) or html.escape(JUDGE_MODEL)
    judge_line = f'judge {judge_str} vision · iOS Simulator' if judge_str else 'iOS Simulator'

    P.append(nav_bar("report", report_name, explorer_name))

    if best:
        bm, btl, bcompletion, _, _, _ = best
        baseline, _ = grade(rows, lambda k, r, bm=bm: k[0] == bm and k[1] == BASELINE_TOOL)
        lift_points = round((bcompletion - (baseline or 0)) * 100)
        hero_title = f'AppControlBench'
        # hero_copy = (f'Same model, same tasks: {html.escape(mlabel(bm))} rose from {pct(baseline)} without tools '
        #              f'to {pct(bcompletion)} with {html.escape(tl_label(btl))}.')
        hero_copy = 'Compare models, tools, cost, and execution evidence across real iOS app-control tasks.'
    else:
        hero_title = 'See what actually completes the task.'
        hero_copy = 'Compare models, tools, cost, and execution evidence across real iOS app-control tasks.'

    atlas_label = (f'A Bayer-dithered dot lattice forming {atlas_number} — the best '
                   f'configuration’s completion score')
    P.append('<main id="top">'
             '<section class="page-hero">'
             # full-bleed canvas: the dot lattice is a compute-shader spring simulation that
             # repels from the cursor. .atlas-anchor (right column, 34/21) marks where the number
             # sits — the JS pins the glyph to that box, so the digits stay the size/place they
             # had in the two-column layout.
             f'<figure class="outcome-atlas" role="img" aria-label="{atlas_label}">'
             '<div class="atlas-stage">'
             f'<canvas id="outcome-atlas-canvas" data-total="{atlas_number}" aria-hidden="true"></canvas>'
             '<svg class="atlas-number" viewBox="0 0 680 420" preserveAspectRatio="xMidYMid meet" '
             'aria-hidden="true"><text x="340" y="210" font-family="Arial, sans-serif" '
             'font-size="345" font-weight="900" text-anchor="middle" dominant-baseline="central">'
             f'{atlas_number}</text></svg></div></figure>'
             '<div class="hero-grid">'
             '<div class="hero-copy">'
             f'<h1>{hero_title}</h1><p class="hero-lead">{hero_copy}</p>'
             '</div>'
             '<div class="atlas-anchor" aria-hidden="true"></div>'
             '</div>'
             '</section>')

    P.append('<section class="leaderboard-section" id="leaderboard">'
             '<header class="section-intro"><div><h2>Leaderboard</h2>'
             '<p>Completion score gives partial credit for partial task completion.</p></div>'
             '</header><div class="leaderboard-wrap">'
             '<table class="leaderboard"><thead><tr><th>Rank</th><th>Configuration</th><th>Tool</th>'
             '<th>Completion</th><th>TIME / RUN</th><th>Cost / run</th><th>Score</th>'
             '</tr></thead><tbody>')
    for rank, (m, tl, completion, avg_s, price, n) in enumerate(leaders[:10], 1):
        top = ' class="top-rank"' if rank <= 3 else ''
        tool_ver = TOOL_VERSIONS.get(tl)
        tool_meta = f'<small>v{html.escape(tool_ver)}</small>' if tool_ver else ''
        prov = provider_of(m)
        fill_cls = 'lb-fill' + (f' {prov}' if prov in ("anthropic", "openai") else '')
        w = max(completion * 100, 2.0) if completion is not None else 0.0
        score_bar = f'<span class="{fill_cls}" style="width:{w:.1f}%"></span>' if completion is not None else ''
        P.append(f'<tr{top}><td class="rank-pos">{rank:02}</td>'
                 f'<th scope="row"><span class="rank-model">{mlogo(m)}{html.escape(mlabel(m))}</span></th>'
                 f'<td><span class="rank-tool">{html.escape(tl_label(tl))}</span>{tool_meta}</td>'
                 f'<td class="num">{pct(completion)}</td><td class="num">{fmt_time(avg_s)}</td>'
                 f'<td class="num">{fmt_price(price)}</td>'
                 f'<td class="lb-score"><span class="lb-track">{score_bar}</span></td></tr>')
    P.append('</tbody></table></div></section>')

    # (pollution banner removed — the per-run 'stale' chips already mark rerun-designated results, and
    # stale runs are excluded from all score aggregates, so a page-level warning is redundant.)

    P.append('<section class="compare-section" id="compare">')
    P.append(sec("Tool comparison",
                 'Completion score by model and tool, partial outcomes at half credit. Every judged run '
                 'counts, failures included; judge errors and stale reruns do not.'))

    # model toggles — check/uncheck to hide a model from EVERY stat (re-derived client-side). Default-hidden
    # models start unchecked; the detail grid keeps their columns (revealed on toggle), so no data is lost.
    P.append('<div class="mtoggles"><span class="mtlabel">show models</span>')
    for m in models:
        chk = '' if default_hidden(m) else ' checked'
        cls = 'mtog' + ('' if chk else ' off')
        P.append(f'<label class="{cls}" data-model="{m}"><input type="checkbox" class="mtcb" '
                 f'data-model="{m}"{chk}><span>{mlogo(m)}{html.escape(mlabel(m))}</span></label>')
    P.append('</div>')

    # Completion score by model and tool. Overall is shown on both axes.
    P.append('<div class="matrix-wrap"><section class="hero" id="stat-hero"><div class="hlabel">completion score by model and tool</div>')
    P.append('<table class="mt"><thead><tr><th class="rh">model</th>')
    for tl in TOOLS:
        ver = TOOL_VERSIONS.get(tl)
        vsub = f'<span class="tvh">v{html.escape(ver)}</span>' if ver else ''
        P.append(f'<th>{html.escape(tl_label(tl))}{vsub}</th>')
    P.append('<th class="ov">overall</th></tr></thead><tbody>')
    # top score across the per-tool data cells -> heatmap scale (the heatmap tweak shades cells by frac)
    hero_max = max((grade(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl)[0] or 0
                    for m in vis for tl in TOOLS), default=0) or 1.0
    def hero_row(rowcls, family, label_html, pred):   # one <tr>: per-tool cells (best) + overall; family drives colour
        cellvals = {tl: grade(rows, lambda k, r, tl=tl: pred(k) and k[1] == tl) for tl in TOOLS}
        rowbest = max([p for p, n in cellvals.values() if p is not None], default=None)
        fam = f' data-family="{family}"' if family else ''
        P.append(f'<tr class="{rowcls}"{fam}>{label_html}')
        for tl in TOOLS:
            p, n = cellvals[tl]
            win = p is not None and rowbest is not None and rowbest > 0 and p == rowbest
            P.append(mt_cell(p, n, win, (p / hero_max) if p is not None else 0.0))
        po, no = grade(rows, lambda k, r: pred(k))
        P.append(ovc_cell(po, no) + '</tr>')

    # Group models by family: a family with >1 thinking variant gets an aggregate parent row + one
    # indented child row per thinking level; a lone model stays a single row. data-family colours the row.
    for base, members in model_groups(vis):
        fam = provider_of(members[0]) or ""
        if len(members) == 1:
            m = members[0]
            hero_row("grp-parent", fam, f'<th class="rh">{mlogo(m)}{mlabel(m)}</th>', lambda k, m=m: k[0] == m)
            continue
        mset = set(members)
        hero_row("grp-parent", fam, f'<th class="rh">{mlogo(members[0])}{html.escape(base)}</th>',
                 lambda k, mset=mset: k[0] in mset)
        for m in members:
            hero_row("grp-child", fam, f'<th class="rh sub">{html.escape(think_level(m))}</th>',
                     lambda k, m=m: k[0] == m)
    # overall row (per tool) + grand total
    P.append('</tbody><tfoot><tr><th class="rh ov">overall</th>')
    for tl in TOOLS:
        pt, nt = grade(rows_vis, lambda k, r, tl=tl: k[1] == tl)
        P.append(ovc_cell(pt, nt))
    pg, ng = grade(rows_vis, lambda k, r: True)
    P.append(ovc_cell(pg, ng, " grand"))
    P.append('</tr></tfoot></table></section></div></section>')

    # Breakdowns use completion score (success=1, partial=.5, fail=0) plus successful-run time.
    # top; horizontal per-model charts (success rate + avg time) side by side below. Time bars scale
    # within their own panel and count successful runs only (failures/partials/timeouts excluded);
    # `n` = those runs.
    P.append('<section class="breakdowns-section" id="breakdowns">')
    P.append(sec("Breakdowns",
                 'Completion score counts every judged run, failures included; judge errors and stale '
                 'reruns are excluded. Average time uses successful runs only - failures, partials and '
                 'timeouts are excluded. '
                 f'The per-model panels exclude {html.escape(tl_label(BASELINE_TOOL))} runs.'))
    P.append('<div id="stat-breakdowns">')
    # The panel titles carry no "· by tool" / "· by model": the top pair is labelled with tool names and the
    # bottom pair with model names, so the axis says it already.
    P.append('<div class="grid-2">')
    P.append(panel_bars_vertical("completion score",
                                 [(tl_label(tl), *grade(rows_vis, lambda k, r, tl=tl: k[1] == tl)) for tl in TOOLS]))
    P.append(time_bars_vertical("avg time / task",
                                [(tl_label(tl), *avg_time_success(rows_vis, lambda k, r, tl=tl: k[1] == tl)) for tl in TOOLS]))
    P.append('</div>')
    P.append('<div class="grid-sm">')
    P.append(panel_bars("completion score",
                        [(mlabel(m), *grade(rows, lambda k, r, m=m: k[0] == m and k[1] != BASELINE_TOOL), mlogo(m)) for m in vis]))
    P.append(time_bars("avg time / task",
                       [(mlabel(m), *avg_time_success(rows, lambda k, r, m=m: k[0] == m and k[1] != BASELINE_TOOL), mlogo(m)) for m in vis]))
    P.append('</div>')
    P.append('</div>')  # #stat-breakdowns
    P.append('</section>')

    # Cost efficiency: average price per task vs completion score, one point per model and tool.
    # Tools are NOT collapsed: argent / agent-device / no-tool are separate dots, shape-coded. The chart
    # initially shows `vis`, while every model supplies its stable x-axis domain.
    price_pts = [(mlabel(m), tl,
                  avg_price(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl)[0],
                  *grade(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl),
                  provider_of(m) or "") for m in vis for tl in TOOLS]
    scale_price_pts = [(mlabel(m), tl,
                        avg_price(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl)[0],
                        *grade(rows, lambda k, r, m=m, tl=tl: k[0] == m and k[1] == tl),
                        provider_of(m) or "") for m in models for tl in TOOLS]
    if any(p is not None for _, _, p, _, _, _ in price_pts):
        P.append('<section class="efficiency-section" id="efficiency">')
        P.append(sec("Cost efficiency",
                     'Average price per task vs completion score, both over every judged run - failures '
                     'included, so a model that fails cheaply is not flattered. Colour identifies the '
                     'model, shape the tool; higher and cheaper is better.'))
        P.append(price_scatter(price_pts, scale_price_pts))
        P.append('</section>')

    method_index = "".join(
        f'<button type="button" data-example="{i}" aria-current="{"true" if i == 0 else "false"}">'
        f'<span class="method-task-top"><span class="method-eyebrow">{html.escape(r["app"])} · '
        f'{html.escape(r["activity"])}</span><span class="method-chip {r["verdict"]}"><i></i>'
        f'{html.escape(r["verdict"])}</span></span><span class="method-task-title">{html.escape(r["short"])}</span>'
        f'<span class="method-task-meta">{html.escape(r["time"])}</span></button>'
        for i, r in enumerate(method_examples))
    methodology_examples_html = (
        '<div class="method-walkthrough">'
        '<div class="method-rail">'
        '<div><span class="method-eyebrow">01 · Arrange</span><b>A fresh simulator clone and re-seeded app state; '
        'the agent gets only the task text and its tool</b></div>'
        '<div><span class="method-eyebrow">02 · Act</span><b>It drives the device until it decides it is done or the clock runs out</b></div>'
        '<div><span class="method-eyebrow">03 · Capture</span><b>The final screenshot</b></div>'
        '<div><span class="method-eyebrow">04 · Judge</span><b>A vision model matches that against the judge screen description</b></div>'
        '</div>'
        f'<div class="method-example-layout" id="method-examples"><nav class="method-task-index" '
        f'aria-label="Worked task examples">{method_index}</nav><div class="method-work" id="method-task-detail"></div></div>'
        '<p class="method-limit-note">The judge is never told which model produced a screenshot, though the action names reveal '
        'which tool drove the device. It cannot see intermediate screens, so a run that reaches the right end state by a wrong route '
        'still scores as a success. That is the main known weakness of final-state grading.</p></div>'
        if method_examples else "")

    P.append('<section class="methodology-section" id="methodology">'
             '<header class="section-intro"><div><h2>Methodology</h2>'
             '</div>'
             f'<a href="{REPO_URL}" target="_blank" rel="noopener">Read more</a></header>'
             f'{methodology_examples_html}</section></main>'
             '<footer class="site-footer"><div class="provenance">'
             f'<span>Generated {now}</span><span>{judge_line}</span>'
             + (f'<span>{tools_ver_str}</span>' if tools_ver_str else '')
             + (f'<span>{app_ver_str}</span>' if app_ver_str else '')
             + '</div></footer>')

    # Run transcripts embedded for the explorer's detail modal.t l
    chats = {f"{m}__{tl}__{tid}": r["chat"] for (m, tl, tid), r in rows.items() if r.get("chat")}
    # Redact absolute local paths from the embedded transcripts before shipping — the repo root (which
    # carries the internal repo name) and the home dir leak into every tool-call's cwd/path.
    chats_json = json.dumps(chats, separators=(',', ':'))
    chats_json = chats_json.replace(ROOT, "<repo>").replace(os.path.expanduser("~"), "~")
    # Neutralize < > & (and the JS line separators) in ALL embedded JSON so transcript data — which can
    # contain literal </script>, </body>, <!-- ... — can never break out of the <script> tag or confuse
    # the HTML parser (or a downstream injector like preview.py's live-reload snippet). \uXXXX escapes
    # decode back to the same characters at runtime, so the modal still shows the real text.
    def script_safe(s):   # json.dumps(ensure_ascii=True) already \u-escapes line separators / non-ASCII
        return s.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    embed = lambda obj: script_safe(json.dumps(obj, separators=(',', ':')))
    # per (model,tool,task) cell + task meta, so TOGGLE_JS can re-derive every aggregate for any subset.
    q_cells = [[m, tl, t["id"],
                (rows.get((m, tl, t["id"])) or {}).get("verdict") or None,
                ((rows.get((m, tl, t["id"])) or {}).get("meta") or {}).get("wall_s"),
                (rows.get((m, tl, t["id"])) or {}).get("cost"),
                unit_state(m, tl, t["id"])]
               for m in models for tl in TOOLS for t in tasks]
    q_stats = {"tools": list(TOOLS), "tlabels": {tl: tl_label(tl) for tl in TOOLS},
               "baseline": BASELINE_TOOL,   # excluded from the breakdowns — see BASELINE_TOOL
               "labels": {m: mlabel(m) for m in models},
               "tasks": {t["id"]: bool(t.get("annulled")) for t in tasks}, "cells": q_cells}

    # ---- run-detail modal data (left column: the run + the judge's telemetry) ------------------------
    # The judge is shown the task's prompt + solved_screen and the agent's tool-call list. Since 2026-07,
    # judge.py records that verbatim in score.json as `judge_input`; the ~470 runs scored before it did
    # have no such key (and are not re-judged unless --rejudge), so the modal reconstructs the same facts
    # from tasks.json + the run's meta.json and says so. `ji` below is therefore emitted ONLY when the
    # judge itself recorded it — otherwise the client falls back to Q_TASKINFO + the run's own meta.
    # The task prompt / solved_screen live in Q_TASKINFO (once per task, not once per run) and the full
    # judge_prompt is deliberately NOT embedded: it is ~2.3KB of near-identical rubric boilerplate per
    # run (~1MB over a full re-judge) and every task-specific line in it is already a field here.
    q_taskinfo = {t["id"]: {"app": t.get("app") or "", "kind": t.get("kind") or "",
                            "prompt": t.get("prompt") or "",
                            "solved": t.get("solved_screen") or ""} for t in tasks}
    q_runs = {}
    for (m, tl, tid), r in rows.items():
        meta = r["meta"] or {}
        d = {"m": mlabel(m), "tl": tl_label(tl), "task": tid,
             "app": (tmap.get(tid) or {}).get("app") or meta.get("app") or "",
             "st": unit_state(m, tl, tid),
             "v": r["verdict"], "conf": r["conf"], "reason": r["reason"] or "", "jm": r["judge"] or "",
             "wall": meta.get("wall_s"), "ntc": meta.get("n_tool_calls"),
             "tn": meta.get("tool_names") or [], "cost": r.get("cost"),
             "to": bool(meta.get("timed_out"))}
        if r.get("png"):
            d["shot"] = f"{artifacts_rel}/{m}__{tl}__{tid}.png"
        if r.get("judge_input"):
            d["ji"] = r["judge_input"]        # the judge's own record — preferred over the reconstruction
        q_runs[f"{m}__{tl}__{tid}"] = d
    # judge reasons / task text can't carry local paths today, but redact like the transcripts anyway
    def embed_redacted(obj):
        s = json.dumps(obj, separators=(',', ':'))
        return script_safe(s.replace(ROOT, "<repo>").replace(os.path.expanduser("~"), "~"))

    # small, needed by both pages (TOGGLE_JS on the report, DRAWER_JS on the explorer)
    jsdata_common = ("const Q_MODELS=%s;const Q_MLABELS=%s;"
                     % (embed(list(models)), embed({m: mlabel(m) for m in models})))
    # report-only: drives the client-side re-derivation of hero/breakdowns/scatter on model toggle
    jsdata_toggle = ("const Q_STATS=%s;const Q_DEFHID=%s;const Q_LOGOS=%s;const Q_FAMILY=%s;const Q_TOOLVER=%s;"
                      % (embed(q_stats), embed([m for m in models if default_hidden(m)]),
                         embed({m: mlogo(m) for m in models}),
                         embed({m: (provider_of(m) or "") for m in models}),
                         embed(TOOL_VERSIONS)))
    jsdata_method = "const METHOD_EXAMPLES=%s;" % embed_redacted(method_examples)
    # explorer-only: the (heavy) per-run transcripts and modal facts — kept off the report page entirely
    jsdata_modal = ("const Q_CHATS=%s;const Q_RUNS=%s;const Q_TASKINFO=%s;"
                     % (script_safe(chats_json), embed_redacted(q_runs), embed_redacted(q_taskinfo)))

    P.append(f'<script>{jsdata_common}{jsdata_toggle}{jsdata_method}'
             f'{TOGGLE_JS}{METHODOLOGY_JS}{TWEAKS_JS}{GPU_JS}</script>')
    P.append('</div></body></html>')
    report_html = "".join(P)

    # ---------------------------------------------------------------------------------------------
    # Run explorer — a bounded focus matrix over the same per-run facts used by the report.  The
    # client owns only view state (focus, filtering and selection); all run facts originate here.
    # ---------------------------------------------------------------------------------------------
    explorer_tasks = [
        t for t in tasks
        if t.get("annulled")
        or any(unit_state(m, tl, t["id"]) != "pending" for m in models for tl in TOOLS)
    ]
    q_explorer = {
        "configs": [{
            "id": f"{m}__{tl}", "modelLabel": mlabel(m), "base": base_name(m),
            "effort": think_level(m), "tool": tl, "toolLabel": tl_label(tl),
        } for m, tl in cells],
        "tasks": [{
            "id": t["id"], "app": t.get("app") or "", "kind": t.get("kind") or "",
            "prompt": t.get("prompt") or "", "annulled": bool(t.get("annulled")),
        } for t in explorer_tasks],
        "tools": list(TOOLS), "toolLabels": {tl: tl_label(tl) for tl in TOOLS},
        "toolShort": {tl: ("no tool" if tl == "none" else tl) for tl in TOOLS},
        "effortRank": EFF_RANK,
    }
    jsdata_explorer = "const Q_EXPLORER=%s;" % embed_redacted(q_explorer)

    Pe = doc_head(f"{title} - Run explorer", "run-explorer-page")
    Pe.append(tw_panel_html)
    Pe.append(nav_bar("explorer", report_name, explorer_name))
    Pe.append('<main id="top"><section class="runs-section" id="runs">')
    Pe.append('<header class="rx-page-head"><span>RUN EXPLORER</span><h1>How configurations handled each task</h1>'
              '<p>Choose a small focus set, then inspect every remaining configuration in the field.</p></header>'
              '<div id="run-explorer-root" aria-live="polite"><div class="rx-loading">Loading run explorer...</div></div>'
              '<div id="run-peek" class="rx-peek" role="tooltip" hidden></div>'
              '<div id="run-toast" class="rx-toast" role="status" hidden></div></section></main>')
    Pe.append('<footer class="site-footer"><span>AppControlBench</span>'
              f'<a class="ghl" href="{REPO_URL}" target="_blank" rel="noopener">{GITHUB_LOGO}Source and data</a></footer>')

    Pe.append(f'<script>{jsdata_common}{jsdata_modal}{jsdata_explorer}{RUN_EXPLORER_JS}{TWEAKS_JS}</script>')
    Pe.append('</div></body></html>')
    explorer_html = "".join(Pe)

    return report_html, explorer_html


def main():
    ap = argparse.ArgumentParser()
    desk = os.path.expanduser("~/Desktop")
    ap.add_argument("--results", default=os.path.join(ROOT, "results"))
    ap.add_argument("--out", default=os.path.join(desk, "benchmark-report.html"))
    ap.add_argument("--artifacts", default=os.path.join(desk, "benchmark-artifacts"))
    ap.add_argument("--title", default="Agent tooling benchmark")
    a = ap.parse_args()

    tasks, tmap = load_tasks()
    # Optional app filter (env, non-breaking): REPORT_APPS=element restricts the whole report to those
    # apps — used for inspecting a single-app run so empty task columns (e.g. bsky) don't clutter it.
    _apps = os.environ.get("REPORT_APPS")
    if _apps:
        keep = set(_apps.split(","))
        tasks = [t for t in tasks if t["app"] in keep]
        tmap = {t["id"]: t for t in tasks}
    rows = collect(a.results)
    # Decide the visible model set once, then drop hidden-model rows — so screenshots copied, aggregates
    # logged, and the rendered report all agree (no internal screenshots on disk / no skew in public mode).
    if _apps:
        rows = {k: v for k, v in rows.items() if k[2] in tmap}   # drop out-of-scope-app runs
    models = shown_models(rows)
    # Optional model filter (env, non-breaking): REPORT_MODELS=gpt_low,gpt_high,... restricts the report
    # to exactly those models instead of the full roster — for inspecting a specific run's cells.
    _mods = os.environ.get("REPORT_MODELS")
    if _mods:
        keep = [m for m in _mods.split(",") if m in MODELS]
        models = [m for m in keep if m in models] or keep   # preserve requested order
    rows = {k: v for k, v in rows.items() if k[0] in set(models)}
    os.makedirs(a.artifacts, exist_ok=True)
    artifacts_rel = os.path.basename(a.artifacts.rstrip("/"))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    for (m, tl, tid), r in rows.items():
        if r["png"]:
            try:
                shutil.copy(r["png"], os.path.join(a.artifacts, f"{m}__{tl}__{tid}.png"))
            except Exception:
                pass

    out_dir = os.path.dirname(a.out)
    report_name = os.path.basename(a.out)
    base, ext = os.path.splitext(report_name)
    explorer_name = f"{base}-runs{ext}"
    explorer_out = os.path.join(out_dir, explorer_name)

    report_html, explorer_html = build(tasks, tmap, rows, models, a.title, artifacts_rel, now,
                                        report_name=report_name, explorer_name=explorer_name)
    open(a.out, "w").write(report_html)
    open(explorer_out, "w").write(explorer_html)
    n_scored = sum(1 for k, r in rows.items() if r["verdict"] and k[2] not in ANNULLED)
    overall, _ = grade(rows, lambda k, r: True)
    print(f"report -> {a.out}  ({n_scored} scored, overall {pct(overall)})")
    print(f"run explorer -> {explorer_out}")


if __name__ == "__main__":
    main()
