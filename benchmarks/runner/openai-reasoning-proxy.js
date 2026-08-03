#!/usr/bin/env node
/* Pass-through proxy for the OpenAI API that SETS reasoning_effort at a chosen level.
 *
 * Mirrors anthropic-thinking-proxy.js: opencode 1.4.3 won't take a suffixed/custom model id and its
 * config path for reasoning is unreliable, so we point opencode's openai baseURL here and set the
 * effort at the HTTP layer. The model runs through the SAME opencode tool-loop.
 *
 * Effort per family is FIXED for the proxy's lifetime via the BENCH_EFFORT_JSON env var:
 *     BENCH_EFFORT_JSON='{"gpt54": "high", "gpt55": "medium"}'   // absent family -> untouched
 * The harness starts a FRESH proxy per run (runner/isolation.py), so there is no shared mutable
 * control file and nothing to race. Levels: none | low | medium | high | xhigh. Injects
 * `reasoning_effort` (chat/completions) or `reasoning:{effort}` (responses API), whichever shape
 * the request is.
 *
 * Run: BENCH_EFFORT_JSON='{...}' node openai-reasoning-proxy.js [PORT]   (default 8790)
 * Configure opencode: provider.openai.options.baseURL = "http://localhost:8790/v1"
 */
const http = require("http"), https = require("https");
const PORT = parseInt(process.argv[2] || process.env.PROXY_PORT || "8790", 10);
let EFFORT_MAP = {};
try { EFFORT_MAP = JSON.parse(process.env.BENCH_EFFORT_JSON || "{}"); } catch (_) {}
const TARGET = "api.openai.com";
const EFFORTS = new Set(["none", "low", "medium", "high", "xhigh"]);  // gpt-5.4-mini/5.5 valid values ('none' = no reasoning)
let injected = 0;

function effortFor(family) {
  return EFFORTS.has(EFFORT_MAP[family]) ? EFFORT_MAP[family] : null;
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    let body = Buffer.concat(chunks);
    if (req.method === "POST" && (req.url.includes("/chat/completions") || req.url.includes("/responses")) && body.length) {
      try {
        const j = JSON.parse(body.toString("utf8"));
        if (typeof j.model === "string") {
          const family = j.model.includes("gpt-5.4") ? "gpt54"
                       : j.model.includes("gpt-5.5") ? "gpt55" : null;
          const effort = family && effortFor(family);
          if (effort) {
            if (Array.isArray(j.input)) j.reasoning = { effort };      // responses API
            else j.reasoning_effort = effort;                          // chat/completions
            body = Buffer.from(JSON.stringify(j), "utf8");
            injected++;
            process.stderr.write(`[proxy] reasoning_effort=${effort} -> ${family} req #${injected}\n`);
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
server.listen(PORT, "127.0.0.1", () => process.stderr.write(`[proxy] openai reasoning-proxy on :${PORT} (effort=${JSON.stringify(EFFORT_MAP)})\n`));
