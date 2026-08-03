// Static prerender. Runs after the Python exporter and the client Vite build, and overwrites
// public/index.html and public/index-runs.html with fully rendered markup.
//
// This is where ReportInitial is assembled, and the only place a payload becomes markup. Python
// contributes facts (run-index.json, report-meta.json); the derived view is deriveReportView's output,
// computed here over the full eligible-model catalog.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { h } from 'preact';
import { render } from 'preact-render-to-string';

import type { BuildManifest, ReportInitial, ReportMeta, RunIndex } from '../src/shared/contract';
import { serializeInlineJson } from '../src/shared/inline-json';
import { deriveReportView } from '../src/shared/metrics';
import { ExplorerPage } from '../src/explorer/ExplorerPage';
import { ReportPage } from '../src/report/ReportPage';

// Anchored to an explicit arg, not to import.meta.url or cwd: this file is the SSR *entry*, so at
// runtime it lives in .vite-ssr/ rather than web/scripts/, and a path relative to the module would
// point one directory too high. The build script passes the repo root (web/package.json's `..`)
// since npm runs this with cwd = web/, one level below public/.
const PUBLIC = resolve(process.argv[2] ?? process.cwd(), 'public');

const SUPPORTED_SCHEMA = 2;

type ViteManifest = Record<
  string,
  { file: string; css?: string[]; imports?: string[]; isEntry?: boolean }
>;

function readJson<T>(path: string): T {
  return JSON.parse(readFileSync(path, 'utf8')) as T;
}

/**
 * Hard-fail rather than deploy a partially rendered or silently misinterpreted report
 * (docs/report-frontend-contract.md:270).
 */
function requireSchema(name: string, version: number): void {
  if (version !== SUPPORTED_SCHEMA) {
    throw new Error(
      `${name} has schemaVersion ${version}; this prerenderer supports ${SUPPORTED_SCHEMA}. ` +
        `Refusing to emit a page from a resource it may misread.`,
    );
  }
}

/** The entry's own JS, plus every stylesheet reachable through its imported chunks. */
function assetsFor(manifest: ViteManifest, entry: string): { script: string; styles: string[] } {
  const root = manifest[entry];
  if (!root) throw new Error(`no manifest entry for ${entry}; run the client build first`);
  const styles: string[] = [];
  const seen = new Set<string>();
  const walk = (key: string): void => {
    if (seen.has(key)) return;
    seen.add(key);
    const chunk = manifest[key];
    if (!chunk) return;
    for (const css of chunk.css ?? []) if (!styles.includes(css)) styles.push(css);
    for (const imported of chunk.imports ?? []) walk(imported);
  };
  walk(entry);
  return { script: root.file, styles };
}

function documentHtml(options: {
  title: string;
  bodyClass?: string;
  rootId: string;
  markup: string;
  payloadId: string;
  payload: string;
  script: string;
  styles: string[];
}): string {
  const { title, bodyClass, rootId, markup, payloadId, payload, script, styles } = options;
  const body = bodyClass ? ` class="${bodyClass}"` : '';
  const links = styles.map((href) => `<link rel="stylesheet" crossorigin href="/${href}">`).join('');
  // data-tw-heatmap and data-tw-heat-curve are load-bearing: runner/report.css:104-126 keys the whole
  // hero-matrix heatmap off them. data-tw-explain stays absent, which is what keeps the [i] tooltips
  // rather than inline section captions.
  return (
    '<!doctype html><html lang="en" data-tw-heatmap="on" data-tw-heat-curve="exp">' +
    '<head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    `<title>${escapeHtml(title)}</title>${links}` +
    `<script type="module" crossorigin src="/${script}"></script>` +
    `</head><body${body}>` +
    `<div class="wrap" id="${rootId}">${markup}</div>` +
    `<script type="application/json" id="${payloadId}">${payload}</script>` +
    '</body></html>'
  );
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function main(): void {
  const manifest = readJson<ViteManifest>(resolve(PUBLIC, '.vite/manifest.json'));
  const runIndex = readJson<RunIndex>(resolve(PUBLIC, 'data/v1/run-index.json'));
  const meta = readJson<ReportMeta>(resolve(PUBLIC, 'data/v1/report-meta.json'));
  requireSchema('run-index.json', runIndex.schemaVersion);
  requireSchema('report-meta.json', meta.schemaVersion);

  const manifestBlock: BuildManifest = meta.manifest;
  requireSchema('BuildManifest', manifestBlock.schemaVersion);

  // ReportInitial is never written to disk as its own resource — the browser reads it from the page.
  const initial: ReportInitial = {
    schemaVersion: 2,
    models: meta.models,
    tools: meta.tools,
    view: deriveReportView(runIndex),
    provenance: meta.provenance,
    methodExamples: meta.methodExamples,
    manifest: manifestBlock,
  };

  const report = assetsFor(manifest, 'index.html');
  writeFileSync(
    resolve(PUBLIC, 'index.html'),
    documentHtml({
      title: 'AppControlBench Results',
      rootId: 'report-root',
      markup: render(h(ReportPage, { initial })),
      payloadId: 'acb-report-initial',
      payload: serializeInlineJson(initial),
      script: report.script,
      styles: report.styles,
    }),
  );

  const explorer = assetsFor(manifest, 'index-runs.html');
  writeFileSync(
    resolve(PUBLIC, 'index-runs.html'),
    documentHtml({
      title: 'AppControlBench Results - Run explorer',
      bodyClass: 'run-explorer-page',
      rootId: 'explorer-root',
      markup: render(h(ExplorerPage, { runIndex, manifest: manifestBlock, provenance: meta.provenance })),
      payloadId: 'acb-explorer-bootstrap',
      // RunIndex is embedded because the Explorer must render and hydrate its matrix immediately
      // (contract line 118); BuildManifest rides along as page bootstrap (line 114) so the drawer can
      // derive resource paths without a second round trip.
      payload: serializeInlineJson({ runIndex, manifest: manifestBlock, provenance: meta.provenance }),
      script: explorer.script,
      styles: explorer.styles,
    }),
  );

  const size = (name: string) => (readFileSync(resolve(PUBLIC, name)).byteLength / 1024).toFixed(1);
  console.log(`prerender -> public/index.html (${size('index.html')} KB)`);
  console.log(`prerender -> public/index-runs.html (${size('index-runs.html')} KB)`);
}

main();
