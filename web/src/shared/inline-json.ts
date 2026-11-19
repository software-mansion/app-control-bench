// The single boundary at which a payload becomes markup.
//
// Everything runner/report_data.py writes is fetched or read as JSON, never pasted into HTML, so
// `report.py`'s `script_safe` (2352) is dropped there. Only the prerenderer embeds a payload in a
// `<script>` tag — `ReportInitial` on the report page, `RunIndex` on the explorer — and this is the one
// function it uses to do it.

const ESCAPES: Record<string, string> = {
  // `</script`, `<!--` and `]]>` all terminate or reopen a script element inside HTML. Escaping the
  // three characters that can start them means no payload can ever break out of its own tag.
  '<': '\\u003c',
  '>': '\\u003e',
  '&': '\\u0026',
  // Valid in JSON strings but line terminators in JavaScript source, so an un-escaped one turns a
  // string literal into a syntax error the moment the page parses.
  '\u2028': '\\u2028',
  '\u2029': '\\u2029',
};

/**
 * `JSON.stringify`, safe to place verbatim inside a `<script>` element. The result is still valid JSON:
 * the escapes are JSON string escapes, so `JSON.parse` returns the original value unchanged.
 */
export function serializeInlineJson(value: unknown): string {
  const json = JSON.stringify(value);
  // `JSON.stringify(undefined)` is `undefined`, not a string. `null` is the only sane inline payload.
  if (json === undefined) return 'null';
  return json.replace(/[<>&\u2028\u2029]/g, (char) => ESCAPES[char]);
}
