import { createReadStream, existsSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import typegpu from 'unplugin-typegpu/vite';

// Client build. Static pages, no router: each HTML input gets its own entry chunk, and Vite emits one
// shared hashed stylesheet that they all link — replacing the 106 KB of CSS report.py inlined twice.
//
// `emptyOutDir: false` because ../public is co-owned: runner/report_data.py writes data/ and artifacts/
// there before this build runs. The `clean` script in package.json therefore removes public/assets and
// public/.vite itself, so obsolete hashed assets can neither accumulate nor deploy.
const root = resolve(import.meta.dirname);
const PUBLIC = resolve(root, '../public');
// The telemetry gate. The screen is internal only, and the tree is what decides: the public
// environment's `paths: exclude` in publish.yaml drops this file, so a public build produces a
// two-page site rather than failing on an input it cannot resolve. runner/report_data.py and
// scripts/prerender.mts gate on the same path, and ci/verify-release-tree.sh fails a tree that
// disagrees with the environment it is built for.
const TELEMETRY_PAGE = resolve(root, 'index-telemetry.html');

const TYPES: Record<string, string> = {
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
};

/**
 * Serve the exporter's output during `vite dev`.
 *
 * `publicDir` would be the obvious way, but it is copied into `outDir` on build — and here `outDir` IS
 * the public directory, so that would be circular. Worse, in dev it would let the prerendered
 * public/index-runs.html shadow web/index-runs.html and serve a page whose hashed asset links do not
 * exist yet. This serves the two exporter-owned trees and nothing else.
 */
function serveExportedData(): Plugin {
  return {
    name: 'acb-serve-exported-data',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0];
        // The favicon is committed under web/static and copied to the site root by the prerenderer, so
        // in production it lives at /favicon.svg. Map that same URL here rather than linking a
        // dev-only path from the head — one href, correct in both modes.
        if (url === '/favicon.svg') {
          res.setHeader('Content-Type', TYPES['.svg']);
          return createReadStream(resolve(root, 'static/favicon.svg')).pipe(res);
        }
        if (!url.startsWith('/data/') && !url.startsWith('/artifacts/')) return next();
        // Refuse anything that escapes the two trees.
        const file = resolve(PUBLIC, '.' + decodeURIComponent(url));
        if (!file.startsWith(PUBLIC + '/')) return next();
        let size: number;
        try {
          const stat = statSync(file);
          if (!stat.isFile()) return next();
          size = stat.size;
        } catch {
          return next();
        }
        const ext = file.slice(file.lastIndexOf('.'));
        res.setHeader('Content-Type', TYPES[ext] ?? 'application/octet-stream');
        res.setHeader('Content-Length', String(size));
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig({
  root,
  // Default would be web/public, which does not exist. Set false so it can never be confused with the
  // repo's real public/ output directory — which is this build's outDir.
  publicDir: false,
  plugins: [typegpu(), serveExportedData()],
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  build: {
    outDir: PUBLIC,
    emptyOutDir: false,
    // The prerenderer reads public/.vite/manifest.json to find the hashed script and stylesheet names.
    manifest: true,
    rollupOptions: {
      input: {
        report: resolve(root, 'index.html'),
        explorer: resolve(root, 'index-runs.html'),
        ...(existsSync(TELEMETRY_PAGE) ? { telemetry: TELEMETRY_PAGE } : {}),
      },
    },
  },
  // The same gate, reaching the bundle: Nav links to the telemetry screen only where the screen
  // exists. A literal rather than a runtime check so the public build drops the link as dead code
  // along with the branch that holds it.
  define: { __HAS_TELEMETRY__: existsSync(TELEMETRY_PAGE) },
});
