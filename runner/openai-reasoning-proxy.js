#!/usr/bin/env node
/* Pass-through proxy for the OpenAI API that SETS reasoning_effort at a chosen level.
 *
 * Mirrors anthropic-thinking-proxy.js: we point opencode's openai baseURL here and set the effort at
 * the HTTP layer, so a cell's effort is fixed by the harness and counted per request, whatever
 * opencode's own default for the model is. The model runs through the SAME opencode tool-loop.
 *
 * Effort per family is FIXED for the proxy's lifetime via the BENCH_EFFORT_JSON env var:
 *     BENCH_EFFORT_JSON='{"gpt6luna": "high", "gpt55": "medium"}'   // absent family -> untouched
 * Families (FAMILIES below): gpt6luna = gpt-6-luna, gpt54 = gpt-5.4*, gpt55 = gpt-5.5*.
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
const EFFORTS = new Set(["none", "low", "medium", "high", "xhigh", "max"]);  // 'none' = no reasoning
const FAMILIES = [["gpt-6-luna", "gpt6luna"], ["gpt-5.4", "gpt54"], ["gpt-5.5", "gpt55"]];  // id fragment -> family
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
          const [, family] = FAMILIES.find(([id]) => j.model.includes(id)) || [];
          // Only the agent's own requests, which carry tools. opencode also asks a small model for a
          // session title, and that model can be the cell's: rewriting it would spend effort on a title.
          const effort = family && Array.isArray(j.tools) && j.tools.length && effortFor(family);
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
// opencode 2 opens the Responses API over a WebSocket first. This proxy rewrites HTTP bodies only, and a
// socket it merely relayed would carry every request past it at the model's default effort. Refuse the
// upgrade: opencode then sends the session's requests as HTTP POSTs, which are rewritten above.
server.on("upgrade", (req, socket) => {
  process.stderr.write(`[proxy] refused websocket upgrade ${req.url}\n`);
  socket.end("HTTP/1.1 426 Upgrade Required\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
});
server.listen(PORT, "127.0.0.1", () => process.stderr.write(`[proxy] openai reasoning-proxy on :${PORT} (effort=${JSON.stringify(EFFORT_MAP)})\n`));
