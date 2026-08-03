#!/usr/bin/env node
/* Pass-through proxy for the Anthropic API that ENABLES extended thinking at a chosen effort.
 *
 * Why: opencode 1.4.3's bundled AI-SDK can't drive the 4.x `thinking:{type:"adaptive"}` + effort API
 * (its catalog has empty reasoning_options and the SDK strips the field), and it also won't accept a
 * suffixed/custom model id. So we point opencode's anthropic baseURL at this proxy; it injects the
 * thinking params at the HTTP layer and forwards everything else untouched — the model runs through the
 * SAME opencode tool-loop as the others, just with thinking engaged.
 *
 * Effort is FIXED for the proxy's lifetime via the BENCH_EFFORT_JSON env var, keyed by family:
 *     BENCH_EFFORT_JSON='{"opus": "medium", "haiku": "low"}'   // absent family -> passthrough
 * The harness starts a FRESH proxy per run (runner/isolation.py), so there is no shared mutable
 * control file and nothing to race. opus defaults to medium if its family key is absent (it should
 * always think; set a non-effort value like "off" to force passthrough). haiku defaults to passthrough.
 *
 * Run: BENCH_EFFORT_JSON='{...}' node anthropic-thinking-proxy.js [PORT]   (default 8788)
 * Configure opencode: provider.anthropic.options.baseURL = "http://localhost:8788/v1"
 */
const http = require("http"), https = require("https");
const PORT = parseInt(process.argv[2] || process.env.PROXY_PORT || "8788", 10);
let EFFORT_MAP = {};
try { EFFORT_MAP = JSON.parse(process.env.BENCH_EFFORT_JSON || "{}"); } catch (_) {}
const TARGET = "api.anthropic.com";
const EFFORTS = new Set(["minimal", "low", "medium", "high"]);
let injected = 0;

function effortFor(family) {
  let e = EFFORT_MAP[family];
  if (family === "opus" && e == null) e = "medium";   // opus always thinks unless overridden
  return EFFORTS.has(e) ? e : null;
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    let body = Buffer.concat(chunks);
    if (req.method === "POST" && req.url.includes("/messages") && body.length) {
      try {
        const j = JSON.parse(body.toString("utf8"));
        if (typeof j.model === "string") {
          const family = j.model.includes("claude-opus-4-8") ? "opus"
                       : j.model.includes("claude-haiku-4-5") ? "haiku" : null;
          const effort = family && effortFor(family);
          if (effort) {
            if (family === "opus") {
              j.thinking = { type: "adaptive" };      // opus-4.8: adaptive + effort (only opus supports this)
              j.output_config = { effort };
            } else {
              // haiku-4.5 and others: STANDARD extended thinking (adaptive is opus-only).
              const BUDGET = { minimal: 1024, low: 4000, medium: 10000, high: 24000 }[effort];
              j.thinking = { type: "enabled", budget_tokens: BUDGET };
              if (!j.max_tokens || j.max_tokens <= BUDGET) j.max_tokens = BUDGET + 8000;  // max_tokens must exceed budget
            }
            delete j.temperature;   // thinking requires default temperature; non-default can 400
            delete j.top_p;
            body = Buffer.from(JSON.stringify(j), "utf8");
            injected++;
            process.stderr.write(`[proxy] thinking(${effort}) -> ${family} req #${injected}\n`);
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
server.listen(PORT, "127.0.0.1", () => process.stderr.write(`[proxy] anthropic thinking-proxy on :${PORT} (effort=${JSON.stringify(EFFORT_MAP)})\n`));
