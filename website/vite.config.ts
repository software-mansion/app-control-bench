import { createReadStream, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import typegpu from 'unplugin-typegpu/vite';

// Client build. Two static pages, no router: each HTML input gets its own entry chunk, and Vite emits
// one shared hashed stylesheet that both link — replacing the 106 KB of CSS report.py inlined twice.
//
// `emptyOutDir: false` because ../public is co-owned: runner/report_data.py writes data/ and artifacts/
// there before this build runs. The `clean` script in package.json therefore removes public/assets and
// public/.vite itself, so obsolete hashed assets can neither accumulate nor deploy.
const root = resolve(import.meta.dirname);
const PUBLIC = resolve(root, '../public');

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
      },
    },
  },
});
