#!/usr/bin/env node
/* Credential-owning effort proxy for Anthropic and OpenAI benchmark routes.
 *
 * OpenCode receives only a placeholder credential. This trusted, separately spawned process owns
 * the real upstream key, replaces any client-supplied authentication, and injects the requested
 * reasoning effort. BENCH_PROXY_PROVIDER selects `anthropic` (default) or `openai`.
 *
 * Anthropic can route either directly or through Vercel AI Gateway by setting
 * BENCH_ANTHROPIC_UPSTREAM=vercel. BENCH_PROXY_UPSTREAM exists for localhost contract tests only;
 * isolation.py deliberately does not copy it from the ambient environment.
 */
const http = require("http"), https = require("https");
const PORT = parseInt(process.argv[2] || process.env.PROXY_PORT || "8788", 10);
let EFFORT_MAP = {};
try { EFFORT_MAP = JSON.parse(process.env.BENCH_EFFORT_JSON || "{}"); } catch (_) {}
const PROVIDER = process.env.BENCH_PROXY_PROVIDER || "anthropic";
if (!new Set(["anthropic", "openai"]).has(PROVIDER)) {
  process.stderr.write(`[proxy] unsupported provider ${PROVIDER}\n`);
  process.exit(78);
}
const USE_VERCEL_GATEWAY =
  PROVIDER === "anthropic" && process.env.BENCH_ANTHROPIC_UPSTREAM === "vercel";
const UPSTREAM_KEY = PROVIDER === "openai"
  ? process.env.OPENAI_API_KEY
  : (USE_VERCEL_GATEWAY ? process.env.AI_GATEWAY_API_KEY : process.env.ANTHROPIC_API_KEY);
if (!UPSTREAM_KEY) {
  process.stderr.write(`[proxy] missing trusted ${PROVIDER} upstream credential\n`);
  process.exit(78);
}
const DEFAULT_UPSTREAM = PROVIDER === "openai"
  ? "https://api.openai.com"
  : (USE_VERCEL_GATEWAY ? "https://ai-gateway.vercel.sh" : "https://api.anthropic.com");
const UPSTREAM = new URL(process.env.BENCH_PROXY_UPSTREAM || DEFAULT_UPSTREAM);
const ANTHROPIC_EFFORTS = new Set(["minimal", "low", "medium", "high"]);
const OPENAI_EFFORTS = new Set(["none", "low", "medium", "high", "xhigh"]);
let injected = 0;

function effortFor(family) {
  let effort = EFFORT_MAP[family];
  if (PROVIDER === "anthropic" && family === "opus" && effort == null) effort = "medium";
  const allowed = PROVIDER === "openai" ? OPENAI_EFFORTS : ANTHROPIC_EFFORTS;
  return allowed.has(effort) ? effort : null;
}

function transformBody(req, body) {
  if (req.method !== "POST" || !body.length) return body;
  const isAnthropicMessages = PROVIDER === "anthropic" && req.url.includes("/messages");
  const isOpenAIRequest = PROVIDER === "openai"
    && (req.url.includes("/chat/completions") || req.url.includes("/responses"));
  if (!isAnthropicMessages && !isOpenAIRequest) return body;

  try {
    const json = JSON.parse(body.toString("utf8"));
    if (typeof json.model !== "string") return body;
    if (PROVIDER === "anthropic") {
      const family = json.model.includes("claude-opus-4-8") ? "opus"
        : json.model.includes("claude-haiku-4-5") ? "haiku" : null;
      const effort = family && effortFor(family);
      if (effort) {
        if (family === "opus") {
          json.thinking = { type: "adaptive" };
          json.output_config = { effort };
        } else {
          const budget = { minimal: 1024, low: 4000, medium: 10000, high: 24000 }[effort];
          json.thinking = { type: "enabled", budget_tokens: budget };
          if (!json.max_tokens || json.max_tokens <= budget) json.max_tokens = budget + 8000;
        }
        delete json.temperature;
        delete json.top_p;
        injected++;
        process.stderr.write(`[proxy] thinking(${effort}) -> ${family} req #${injected}\n`);
      }
      if (USE_VERCEL_GATEWAY && family === "haiku") {
        json.model = "anthropic/claude-haiku-4.5";
      }
    } else {
      const family = json.model.includes("gpt-5.4") ? "gpt54"
        : json.model.includes("gpt-5.5") ? "gpt55" : null;
      const effort = family && effortFor(family);
      if (effort) {
        if (Array.isArray(json.input)) json.reasoning = { effort };
        else json.reasoning_effort = effort;
        injected++;
        process.stderr.write(`[proxy] reasoning_effort=${effort} -> ${family} req #${injected}\n`);
      }
    }
    return Buffer.from(JSON.stringify(json), "utf8");
  } catch (_) {
    return body;
  }
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (chunk) => chunks.push(chunk));
  req.on("end", () => {
    const body = transformBody(req, Buffer.concat(chunks));
    const headers = { ...req.headers, host: UPSTREAM.host };
    delete headers.authorization;
    delete headers["x-api-key"];
    if (PROVIDER === "openai") headers.authorization = `Bearer ${UPSTREAM_KEY}`;
    else headers["x-api-key"] = UPSTREAM_KEY;
    headers["content-length"] = Buffer.byteLength(body);
    delete headers["accept-encoding"];

    const transport = UPSTREAM.protocol === "http:" ? http : https;
    const upstream = transport.request({
      hostname: UPSTREAM.hostname,
      port: UPSTREAM.port || (UPSTREAM.protocol === "http:" ? 80 : 443),
      path: req.url,
      method: req.method,
      headers,
    }, (upstreamResponse) => {
      if (upstreamResponse.statusCode >= 400) {
        process.stderr.write(`[proxy] upstream ${UPSTREAM.host} -> ${upstreamResponse.statusCode}\n`);
      }
      res.writeHead(upstreamResponse.statusCode, upstreamResponse.headers);
      upstreamResponse.pipe(res);
    });
    upstream.on("error", (error) => {
      res.writeHead(502);
      res.end(`proxy upstream error: ${error}`);
    });
    upstream.write(body);
    upstream.end();
  });
});

server.listen(PORT, "127.0.0.1", () => process.stderr.write(
  `[proxy] ${PROVIDER} effort proxy on :${PORT} (upstream=${UPSTREAM.host}, auth=true, effort=${JSON.stringify(EFFORT_MAP)})\n`,
));
