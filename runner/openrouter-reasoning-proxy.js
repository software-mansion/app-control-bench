#!/usr/bin/env node
/* Pass-through proxy for the OpenRouter API that SETS reasoning effort at a chosen level.
 *
 * Third sibling of anthropic-thinking-proxy.js / openai-reasoning-proxy.js, and simpler than both:
 * OpenRouter is one OpenAI-compatible endpoint whose unified `reasoning:{effort}` field it translates
 * per vendor downstream (an OpenAI reasoning_effort, an Anthropic thinking budget). So one injection
 * shape covers every family here — no per-vendor branching, and no budget arithmetic to keep in sync
 * with the Anthropic proxy. Verified against the live API: effort moves reasoning_tokens on both
 * openai/gpt-5.4-mini (none 0 / low 516 / high 5696) and anthropic/claude-haiku-4.5.
 *
 * Effort per family is FIXED for the proxy's lifetime via the BENCH_EFFORT_JSON env var:
 *     BENCH_EFFORT_JSON='{"gpt54": "high", "haiku": "low"}'   // absent family -> untouched
 * The harness starts a FRESH proxy per run (runner/isolation.py), so there is no shared mutable
 * control file and nothing to race.
 *
 * Family keys are the SAME ones the other two proxies use, so a cell's EFFORT entry means the same
 * thing whichever route it takes. Model ids are matched with version dots folded to dashes, because
 * OpenRouter spells a model `anthropic/claude-haiku-4.5` where the native API spells it
 * `claude-haiku-4-5`; folding matches both, and substring matching keeps OpenRouter's `:thinking` /
 * `:nitro` route suffixes working.
 *
 * temperature/top_p are deliberately NOT stripped (the Anthropic proxy must, since thinking 400s on a
 * non-default temperature): OpenRouter normalises both families' sampling params itself, confirmed by
 * a temperature=0.7 + reasoning request returning 200 for haiku-4.5 and gpt-5.4-mini alike.
 *
 * Run: BENCH_EFFORT_JSON='{...}' node openrouter-reasoning-proxy.js [PORT]   (default 8792)
 * Configure opencode: provider.openrouter.options.baseURL = "http://localhost:8792/api/v1"
 *   (the /api/v1 prefix is OpenRouter's own, so request paths forward verbatim like the siblings')
 */
const http = require("http"), https = require("https");
const PORT = parseInt(process.argv[2] || process.env.PROXY_PORT || "8792", 10);
let EFFORT_MAP = {};
try { EFFORT_MAP = JSON.parse(process.env.BENCH_EFFORT_JSON || "{}"); } catch (_) {}
const TARGET = "openrouter.ai";
// Union of both siblings' ladders — every value OpenRouter accepts, so an EFFORT entry written for
// either native proxy is honoured here rather than silently dropped.
const EFFORTS = new Set(["none", "minimal", "low", "medium", "high", "xhigh"]);
let injected = 0;

function effortFor(family) {
  let e = EFFORT_MAP[family];
  if (family === "opus" && e == null) e = "medium";   // opus always thinks unless overridden
  return EFFORTS.has(e) ? e : null;
}

function familyOf(model) {
  const m = model.toLowerCase().replace(/\./g, "-");   // gpt-5.4-mini -> gpt-5-4-mini
  return m.includes("gpt-5-4") ? "gpt54"
       : m.includes("gpt-5-5") ? "gpt55"
       : m.includes("claude-haiku-4-5") ? "haiku"
       : m.includes("claude-opus-4-8") ? "opus"
       // kimi-k3 only: the k2 line (k2, k2-0905, k2-thinking, k2.5/2.6/2.7) reasons but exposes no
       // effort knob, so a generic "kimi" needle would inject a field those models ignore.
       : m.includes("kimi-k3") ? "kimi"
       // qwen3.8-max only, and for the same reason: it is the single qwen id of the 49 OpenRouter
       // serves that lists reasoning_effort — the rest reason without a knob.
       : m.includes("qwen3-8-max") ? "qwen"
       // ox-alpha, not a bare "ox": that would also catch v[ox]tral and any later id spelling the
       // letters. The needle stays exact because a cloaked vendor can add siblings without notice.
       : m.includes("ox-alpha") ? "ox" : null;
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    let body = Buffer.concat(chunks);
    if (req.method === "POST" && req.url.includes("/chat/completions") && body.length) {
      try {
        const j = JSON.parse(body.toString("utf8"));
        if (typeof j.model === "string") {
          const family = familyOf(j.model);
          const effort = family && effortFor(family);
          if (effort) {
            j.reasoning = { effort };
            body = Buffer.from(JSON.stringify(j), "utf8");
            injected++;
            process.stderr.write(`[proxy] reasoning.effort=${effort} -> ${family} req #${injected}\n`);
          }
        }
      } catch (_) { /* forward as-is */ }
    }
    const headers = { ...req.headers, host: TARGET };
    headers["content-length"] = Buffer.byteLength(body);
    delete headers["accept-encoding"];
    const up = https.request({ hostname: TARGET, port: 443, path: req.url, method: req.method, headers },
      (upRes) => { res.writeHead(upRes.statusCode, upRes.headers); upRes.pipe(res); });
    up.on("error", (e) => { res.writeHead(502); res.end(`proxy upstream error: ${e}`); });
    up.write(body); up.end();
  });
});
server.listen(PORT, "127.0.0.1", () => process.stderr.write(`[proxy] openrouter reasoning-proxy on :${PORT} (effort=${JSON.stringify(EFFORT_MAP)})\n`));
