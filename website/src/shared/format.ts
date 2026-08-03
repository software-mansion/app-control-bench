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

/** Seconds as `"12s"` / `"3m 07s"` — the minutes form zero-pads its seconds. */
export function fmtTime(s: number | null | undefined): string {
  if (s === null || s === undefined) return 'n/a';
  const t = pyRound(s);
  return t < 60 ? `${t}s` : `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, '0')}s`;
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
