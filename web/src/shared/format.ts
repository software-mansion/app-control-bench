// Display formatters. The one port of `pct` (runner/report.py:210), `fmt_time` (222) and `fmt_price`
// (238), replacing those plus their `TOGGLE_JS` twins `pct`/`ft`/`fmtPrice` (1372, 1373, 1500).
//
// Python and the old browser twins do not agree, and the disagreement is visible in published numbers:
// Python rounds a float by its EXACT binary value, half-to-even, while `Math.round` and `toFixed` both
// round half away from zero. `pct(0.125)` is "12%" in Python and "13%" under `Math.round`;
// `fmtPrice(0.0625)` is "$0.062" in Python and "$0.063" under `toFixed(3)`. Python is authoritative, so
// `pyFixed` reproduces its rule instead of reaching for the built-ins.

/**
 * Python's `f"{x:.<digits>f}"`: round the double's exact decimal value half-to-even.
 *
 * `toFixed(100)` is the correctly-rounded 100-decimal form of a double, which is the *exact* value for
 * anything with |x| above ~4e-15 — every price, duration and percentage this report handles. Rounding
 * that digit string ourselves is what makes ties resolve the way Python resolves them.
 */
export function pyFixed(x: number, digits: number): string {
  if (!Number.isFinite(x) || Math.abs(x) >= 1e21 || digits < 0 || digits > 90) return x.toFixed(digits);
  const negative = x < 0 || Object.is(x, -0);
  const exact = Math.abs(x).toFixed(100);
  const dot = exact.indexOf('.');
  const intPart = exact.slice(0, dot);
  const frac = exact.slice(dot + 1);
  const keep = frac.slice(0, digits);
  const rest = frac.slice(digits);

  const lead = rest.charCodeAt(0) - 48; // `rest` is never empty: 100 > any digits we format to
  let roundUp = lead > 5;
  if (lead === 5 && !roundUp) {
    const beyondHalf = /[1-9]/.test(rest.slice(1));
    // exactly a half -> half-to-even: round up only when the last kept digit is odd
    const lastKept = (digits > 0 ? keep : intPart).charCodeAt((digits > 0 ? keep : intPart).length - 1) - 48;
    roundUp = beyondHalf || lastKept % 2 === 1;
  }

  const straight = intPart + keep;
  const rounded = roundUp ? increment(straight) : straight;
  const intLength = intPart.length + (rounded.length > straight.length ? 1 : 0);
  const head = rounded.slice(0, intLength).replace(/^0+(?=\d)/, '');
  const tail = rounded.slice(intLength);
  const body = digits > 0 ? `${head}.${tail}` : head;
  return negative ? `-${body}` : body;
}

/** Add one to a non-negative decimal digit string, growing it by a leading '1' on full carry. */
function increment(s: string): string {
  const out = s.split('');
  let i = out.length - 1;
  for (; i >= 0; i--) {
    if (out[i] === '9') out[i] = '0';
    else {
      out[i] = String(out[i].charCodeAt(0) - 48 + 1);
      break;
    }
  }
  if (i < 0) out.unshift('1');
  return out.join('');
}

/** Python's `round(x)`: nearest integer, ties to even. */
export function pyRound(x: number): number {
  return Number(pyFixed(x, 0));
}

/** A 0..1 fraction as a whole percent: `0.73` -> `"73%"`. */
export function pct(p: number | null | undefined): string {
  return p === null || p === undefined ? 'n/a' : `${pyFixed(p * 100, 0)}%`;
}

/**
 * A completion score as a percent with one decimal: `0.9833` -> `"98.3%"`.
 *
 * The matrix ranks cells by a score the heatmap tints continuously, so a whole percent collapses
 * gaps the colour still shows: 59/60 and 58.5/60 both print "98%" while their tints differ by a
 * quarter of the ramp. Every surface that shows a completion score uses this; the verdict
 * distribution and scatter tooltips stay whole, where a decimal is noise.
 */
export function pct1(p: number | null | undefined): string {
  return p === null || p === undefined ? 'n/a' : `${pyFixed(p * 100, 1)}%`;
}

/**
 * Seconds as `"12s"` / `"3m 07s"` / `"3m"` — the minutes form zero-pads its seconds, and drops the
 * seconds part entirely on a whole minute. (`report.py`'s `fmt_time` still writes `"3m 00s"` there.)
 */
export function fmtTime(s: number | null | undefined): string {
  if (s === null || s === undefined) return 'n/a';
  const t = pyRound(s);
  if (t < 60) return `${t}s`;
  const rest = t % 60;
  return rest === 0 ? `${t / 60}m` : `${Math.floor(t / 60)}m ${String(rest).padStart(2, '0')}s`;
}

/** USD per run, always two decimals. A zero or negative price reads as "free". */
export function fmtPrice(p: number | null | undefined): string {
  if (p === null || p === undefined) return 'n/a';
  if (p <= 0) return 'free';
  return `$${pyFixed(p, 2)}`;
}

/** One-decimal SVG coordinate, matching the precision `report.py` writes into its markup. */
export function fmtCoord(n: number): string {
  return pyFixed(n, 1);
}

/** `"gpt-5.4-mini (high)"` / bare `"gpt-5.4-mini"` when there is no effort level. Matches Python's `mlabel()`. */
export function modelEffortLabel(base: string, effort: string | null): string {
  return effort ? `${base} (${effort})` : base;
}

/**
 * Names each tool variant for a view showing exactly `tools`: the toolkit on its own, or with its
 * version appended where that view carries more than one version of it and the two would otherwise
 * read as the same tool. Take the labeller from the VISIBLE set, never the whole catalog — a hidden
 * version is not a reason to put a version number on every label.
 */
export function toolVariantLabels(
  tools: readonly { id: string; toolkit: string; label: string; version: string | null }[],
): (toolId: string) => string {
  const perToolkit = new Map<string, number>();
  for (const tool of tools) perToolkit.set(tool.toolkit, (perToolkit.get(tool.toolkit) ?? 0) + 1);
  const labels = new Map<string, string>();
  for (const tool of tools) {
    const many = (perToolkit.get(tool.toolkit) ?? 0) > 1;
    labels.set(tool.id, many && tool.version ? `${tool.label} v${tool.version}` : tool.label);
  }
  return (toolId) => labels.get(toolId) ?? toolId;
}
