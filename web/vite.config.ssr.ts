import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import typegpu from 'unplugin-typegpu/vite';

// SSR build — deliberately a separate config from the client one.
//
// Two reasons it cannot be folded into vite.config.ts with a `--ssr` flag: pointing `--ssr` at
// multi-page HTML inputs does not work, and sharing an outDir would let this pass overwrite the client
// manifest and publish server-only JavaScript into public/.
//
// Output goes to .vite-ssr/, a gitignored build temp directory that is never deployed. It lives
// under web/, not the repo root: prerender.js runs as a plain Node ESM import, and Node resolves
// `preact` by walking up from the importing file looking for node_modules — which only exists
// under web/, so the output must too.
const root = resolve(import.meta.dirname);

// Nav is server-rendered into the report and explorer documents, so this pass needs the same telemetry
// gate the client build applies. See vite.config.ts.
const HAS_TELEMETRY = existsSync(resolve(root, 'index-telemetry.html'));

export default defineConfig({
  root,
  publicDir: false,
  define: { __HAS_TELEMETRY__: HAS_TELEMETRY },
  plugins: [typegpu()],
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  build: {
    ssr: resolve(root, 'scripts/prerender.mts'),
    outDir: resolve(root, '.vite-ssr'),
    emptyOutDir: true,
    manifest: false,
    // The prerenderer links the client build's hashed assets by reading its manifest; it must not emit
    // a second, competing copy of them.
    ssrEmitAssets: false,
    rollupOptions: {
      output: { entryFileNames: 'prerender.js' },
    },
  },
});
