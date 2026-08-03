#!/usr/bin/env python3
"""Guard: theme.css is the ONLY place a raw style value may live.

Every other file that emits HTML/CSS/SVG must go through a var() token. This is what stops the repo
sliding back into the state it was in before: eight brand hues duplicated between Python and CSS, a
state->colour map written out three times (and already drifted), four ways of spelling the same
shadow, and a live-preview badge carrying its own private palette on top of a themed page.

    python3 runner/check_theme.py          # exit 1 on any violation

Run in CI before the Vercel build (.github/workflows/deploy-vercel.yml).

What is NOT checked, on purpose: one-off padding, margins, grid templates and media-query breakpoints.
They are intra-component layout, not theme — tokenising a value used once is indirection, not a design
system, and media queries cannot read a custom property anyway.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.basename(__file__)
EXEMPT = {"theme.css", SOURCE}          # theme.css holds the values; this file holds the patterns

# A style value is a violation unless it is a var(), a CSS-wide keyword, or a non-colour keyword.
OK = r"(var\(|inherit|initial|unset|none|currentColor|transparent|auto)"
# Same, for a JSX object literal, where the value is a JS string and so carries a quote: the CSS rules
# can look straight at the value, but `fontSize: 'var(--fs-1)'` hides it one character in.
OKQ = r"['\"]?" + OK

RULES = [
    ("colour literal (hex)",      re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3}(?:[0-9a-fA-F]{2})?)?\b")),
    ("colour literal (function)", re.compile(r"\b(?:rgba?|hsla?)\s*\(")),
    # The optional quote is what catches the JSX spelling, `{ color: 'red' }`, as well as CSS's `red`.
    ("named colour",              re.compile(r":\s*['\"]?(?:white|black|red|green|blue|gray|grey|orange|yellow|purple|pink)\b")),
    # NB every lookahead below spans its own leading whitespace, as `:(?!\s*OK)` rather than
    # `:\s*(?!OK)`. With the `\s*` outside, the engine backtracks it to zero width and then evaluates
    # the lookahead against the space itself, where `var(` cannot match — so `font-size: var(--x)`
    # reported a violation. That never surfaced because this repo's CSS is written unspaced
    # (`font-size:var(--x)`, 138 of them, none spaced), but a JSX style object always has the space.
    ("font stack",                re.compile(r"font-family\s*:(?!\s*" + OK + r")")),
    # only a CSS-shaped value (starts with a size or a font keyword) — `font:` is also a common JS
    # object key (e.g. the tweaks-menu prefs blob), and that is not a style declaration.
    ("font shorthand",            re.compile(r"font\s*:(?!\s*" + OK + r")\s*(?=[\d.]|italic|oblique|bold|small-caps|normal)")),
    ("font-size literal",         re.compile(r"font-size\s*:(?!\s*" + OK + r")")),
    ("font-weight literal",       re.compile(r"font-weight\s*:(?!\s*" + OK + r")")),
    ("box-shadow literal",        re.compile(r"box-shadow\s*:(?![^;\"']*(?:var\(|none))")),
    ("z-index literal",           re.compile(r"z-index\s*:(?!\s*" + OK + r")")),
    # JSX style objects spell the same properties in camelCase, so the CSS-shaped rules above miss
    # `style={{ fontSize: 14 }}` entirely. A component must reach for a class and a theme token instead.
    # Custom properties are deliberately NOT matched: `style={{ '--hm': heat }}` passes a number to a
    # token defined in theme.css, which is the sanctioned way to drive a themed value from data. A raw
    # colour inside one is still caught, by the hex/function/named-colour rules.
    ("font stack (JSX)",          re.compile(r"\bfontFamily\s*:(?!\s*" + OKQ + r")")),
    ("font-size literal (JSX)",   re.compile(r"\bfontSize\s*:(?!\s*" + OKQ + r")")),
    ("font-weight literal (JSX)", re.compile(r"\bfontWeight\s*:(?!\s*" + OKQ + r")")),
    ("box-shadow literal (JSX)",  re.compile(r"\bboxShadow\s*:(?![^,}]*(?:var\(|none))")),
    ("z-index literal (JSX)",     re.compile(r"\bzIndex\s*:(?!\s*" + OKQ + r")")),
]

# Lines that legitimately contain one of the patterns above without being a style value.
ALLOW = re.compile(
    r"check_theme|"                       # this guard's own prose
    r"^\s*(#|\*|/\*|//)|"                 # a comment line
    r"stroke-width|paint-order"           # SVG geometry, not colour
)

# Trees scanned, and the file types worth scanning in them. website/src/ is in the walk because the
# report is Preact: a JSX style object is exactly the escape hatch this guard exists to close.
_REPO = os.path.dirname(os.path.dirname(HERE))
ROOTS = (HERE, os.path.join(_REPO, "website", "src"))
EXTS = (".py", ".css", ".ts", ".tsx")
SKIP_DIRS = {"node_modules", "__pycache__", "dist", ".vite", ".vite-ssr"}


def scan(path):
    bad = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if ALLOW.search(line):
                continue
            for label, rx in RULES:
                m = rx.search(line)
                if m:
                    bad.append((n, label, line.strip()[:96]))
                    break
    return bad


def main():
    files = []
    for root in ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            files.extend(os.path.join(dirpath, f) for f in filenames
                         if f.endswith(EXTS) and f not in EXEMPT)
    files.sort()
    total = 0
    for path in files:
        for n, label, line in scan(path):
            total += 1
            print(f"{os.path.relpath(path)}:{n}: {label}\n    {line}")
    if total:
        print(f"\n{total} hardcoded style value(s). Every colour, font, shadow and z-index belongs in "
              f"runner/theme.css — reference it with var(--token).")
        return 1
    print(f"theme guard: clean ({len(files)} files scanned; all style values come from theme.css)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
