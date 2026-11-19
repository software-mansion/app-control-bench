// Static prerender. Runs after the Python exporter and the client Vite build, and overwrites
// public/index.html and public/index-runs.html with fully rendered markup. On an internal tree it also
// writes public/index-telemetry.html. It does so once per edition the export carries: the edition the
// site opens on at the root, every other under public/e/<id>/.
//
// This is where ReportInitial is assembled, and the only place a payload becomes markup. Python
// contributes facts (run-index.json, report-meta.json); the derived view is deriveReportView's output,
// computed here over the full eligible-model catalog.
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { relative, resolve } from 'node:path';

import { h } from 'preact';
import { render } from 'preact-render-to-string';

import type {
  BuildManifest,
  Edition,
  EditionCatalog,
  ReportInitial,
  ReportMeta,
  RunIndex,
} from '../src/shared/contract';
import { editionPrefix, pagePath } from '../src/shared/editions';
import { serializeInlineJson } from '../src/shared/inline-json';
import { deriveReportView } from '../src/shared/metrics';
import { ExplorerPage } from '../src/explorer/ExplorerPage';
import { ReportPage } from '../src/report/ReportPage';

// Anchored to an explicit arg, not to import.meta.url or cwd: this file is the SSR *entry*, so at
// runtime it lives in .vite-ssr/ rather than web/scripts/, and a path relative to the module would
// point one directory too high. The build script passes the repo root (web/package.json's `..`)
// since npm runs this with cwd = web/, one level below public/.
const ROOT = resolve(process.argv[2] ?? process.cwd());
const PUBLIC = resolve(ROOT, 'public');

const SUPPORTED_SCHEMA = 4;

// The telemetry gate: the page's own source file, which publish.yaml keeps out of a public tree. The
// rollup input (vite.config.ts) and data/v1/telemetry.json (runner/report_data.py) key off the same
// path, so a public build emits the two public pages and nothing more.
//
// Nothing here may import from web/src/telemetry/: those sources are excluded too, and a static import
// would make the SSR bundle unbuildable on the very tree the exclusion exists for. So the screen has
// no prerendered markup and mounts in the browser, which suits a page whose data it fetches anyway.
const TELEMETRY_PAGE = resolve(ROOT, 'web/index-telemetry.html');
const HAS_TELEMETRY = existsSync(TELEMETRY_PAGE);

// Absolute origin for canonical/og:url/og:image. Social scrapers reject relative image paths, so
// nothing here can be made origin-agnostic. Overridable for preview deployments.
const SITE_URL = (process.env.SITE_URL ?? 'https://appcontrolbench.swmansion.com').replace(/\/$/, '');
const IS_INDEXED = /^(1|true|yes)$/i.test(process.env.IS_INDEXED ?? '');
const ENABLE_ANALYTICS = /^(1|true|yes)$/i.test(process.env.ENABLE_ANALYTICS ?? '');
const OG_IMAGE = 'og.png';
const FAVICON = 'favicon.svg';
const THEME_COLOR = '#FAFAF9';
const GTM_ID = 'GTM-WR6CLZSF';
const GTM_ENABLED = ENABLE_ANALYTICS && GTM_ID !== '';

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

/**
 * The head's discovery block: robots, theme/PWA chrome, canonical, description, Open Graph, X card.
 *
 * Open Graph is the shared vocabulary — X falls back to it for everything except `twitter:card`,
 * which has no OG equivalent and is what promotes the preview from a thumbnail to a full-width
 * image. `twitter:title`/`twitter:description` are therefore deliberately not emitted: they would
 * be byte-identical duplicates of the OG pair that X already reads.
 *
 * `noindex` is a property of the page rather than of the build: an internal page carries the robots
 * tag even where IS_INDEXED says the site is indexed.
 */
function socialMeta(page: {
  title: string;
  description: string;
  path: string;
  noindex?: boolean;
}): string {
  const url = `${SITE_URL}${page.path}`;
  const tag = (attr: 'property' | 'name', key: string, value: string) =>
    `<meta ${attr}="${key}" content="${escapeHtml(value)}">`;
  return (
    (IS_INDEXED && !page.noindex ? '' : tag('name', 'robots', 'noindex, nofollow')) +
    tag('name', 'theme-color', THEME_COLOR) +
    tag('name', 'color-scheme', 'light') +
    tag('name', 'application-name', 'AppControlBench') +
    tag('name', 'mobile-web-app-capable', 'yes') +
    tag('name', 'apple-mobile-web-app-capable', 'yes') +
    tag('name', 'apple-mobile-web-app-title', 'AppControlBench') +
    tag('name', 'apple-mobile-web-app-status-bar-style', 'default') +
    `<link rel="canonical" href="${escapeHtml(url)}">` +
    tag('name', 'description', page.description) +
    tag('property', 'og:type', 'website') +
    tag('property', 'og:locale', 'en_US') +
    tag('property', 'og:site_name', 'AppControlBench') +
    tag('property', 'og:title', page.title) +
    tag('property', 'og:description', page.description) +
    tag('property', 'og:url', url) +
    tag('property', 'og:image', `${SITE_URL}/${OG_IMAGE}`) +
    tag('property', 'og:image:width', '1200') +
    tag('property', 'og:image:height', '630') +
    tag('property', 'og:image:alt', 'AppControlBench - iOS app-control agent benchmark') +
    tag('name', 'twitter:card', 'summary_large_image')
  );
}

function gtmHead(): string {
  if (!GTM_ENABLED) return '';
  return (
    '<link rel="preconnect" href="https://www.googletagmanager.com">' +
    `<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':` +
    `new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],` +
    `j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=` +
    `'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);` +
    `})(window,document,'script','dataLayer','${GTM_ID}');</script>`
  );
}

function gtmBody(): string {
  if (!GTM_ENABLED) return '';
  return (
    `<noscript><iframe src="https://www.googletagmanager.com/ns.html?id=${GTM_ID}"` +
    ` height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>`
  );
}

function robotsTxt(): string {
  const sitemapLine = `Sitemap: ${SITE_URL}/sitemap.xml`;
  return IS_INDEXED
    ? ['User-agent: *', 'Allow: /', '', sitemapLine, ''].join('\n')
    : ['User-agent: *', 'Disallow: /', '', sitemapLine, ''].join('\n');
}

function sitemapXml(paths: readonly string[]): string {
  const urls = IS_INDEXED
    ? paths
        .map(
          (path) =>
            `  <url>\n    <loc>${escapeXml(`${SITE_URL}${path}`)}</loc>\n    <changefreq>weekly</changefreq>\n  </url>`,
        )
        .join('\n')
    : '';
  return (
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
  );
}

function documentHtml(options: {
  title: string;
  description: string;
  path: string;
  noindex?: boolean;
  bodyClass?: string;
  rootId: string;
  markup: string;
  payloadId: string;
  payload: string;
  script: string;
  styles: string[];
}): string {
  const {
    title,
    description,
    path,
    noindex,
    bodyClass,
    rootId,
    markup,
    payloadId,
    payload,
    script,
    styles,
  } = options;
  const body = bodyClass ? ` class="${bodyClass}"` : '';
  const links = styles.map((href) => `<link rel="stylesheet" crossorigin href="/${href}">`).join('');
  // data-tw-heatmap and data-tw-heat-curve are load-bearing: runner/report.css:104-126 keys the whole
  // hero-matrix heatmap off them. data-tw-explain stays absent, which is what keeps the [i] tooltips
  // rather than inline section captions.
  return (
    '<!doctype html><html lang="en" data-tw-heatmap="on" data-tw-heat-curve="exp">' +
    '<head><meta charset="utf-8">' +
    gtmHead() +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    `<title>${escapeHtml(title)}</title>` +
    `<link rel="icon" type="image/svg+xml" href="/${FAVICON}">` +
    socialMeta({ title, description, path, noindex }) +
    `${links}` +
    `<script type="module" crossorigin src="/${script}"></script>` +
    `</head><body${body}>` +
    gtmBody() +
    `<div class="wrap" id="${rootId}">${markup}</div>` +
    `<script type="application/json" id="${payloadId}">${payload}</script>` +
    '</body></html>'
  );
}

/** Also escapes `"`, because meta/link values below are interpolated into quoted attributes. */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeXml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** One edition's three pages, written under its own prefix (the site root for the default edition). */
function renderEdition(manifest: ViteManifest, catalog: EditionCatalog, edition: Edition): string[] {
  const dataDir = resolve(PUBLIC, 'data/v1/editions', edition.id);
  const runIndex = readJson<RunIndex>(resolve(dataDir, 'run-index.json'));
  const meta = readJson<ReportMeta>(resolve(dataDir, 'report-meta.json'));
  requireSchema(`${edition.id}/run-index.json`, runIndex.schemaVersion);
  requireSchema(`${edition.id}/report-meta.json`, meta.schemaVersion);

  const manifestBlock: BuildManifest = meta.manifest;
  requireSchema(`${edition.id} BuildManifest`, manifestBlock.schemaVersion);
  if (manifestBlock.edition.id !== edition.id || manifestBlock.defaultEditionId !== catalog.defaultEditionId) {
    throw new Error(
      `${edition.id}/report-meta.json does not agree with editions.json about which edition it is ` +
        `or which one the site opens on. Re-run the exporter.`,
    );
  }

  // The default edition is the site: served at the root, indexed, named without a qualifier. Every
  // other edition is an archive of an earlier setup, reachable from the edition control.
  const isDefault = edition.id === catalog.defaultEditionId;
  const outDir = resolve(PUBLIC, '.' + editionPrefix(catalog, edition.id));
  mkdirSync(outDir, { recursive: true });
  const suffix = isDefault ? '' : ` (${edition.label})`;
  const written: string[] = [];
  const write = (name: string, html: string): void => {
    writeFileSync(resolve(outDir, name), html);
    written.push(resolve(outDir, name));
  };

  // ReportInitial is never written to disk as its own resource — the browser reads it from the page.
  const initial: ReportInitial = {
    schemaVersion: 4,
    models: meta.models,
    tools: meta.tools,
    defaultToolIds: meta.defaultToolIds,
    // The DEFAULT tool versions, one per toolkit. Every other version ships in run-index.json and the
    // page re-derives from it when a reader asks for one — this is the view they get without asking.
    view: deriveReportView(runIndex, meta.defaultToolIds),
    provenance: meta.provenance,
    methodExamples: meta.methodExamples,
    manifest: manifestBlock,
  };

  const report = assetsFor(manifest, 'index.html');
  write(
    'index.html',
    documentHtml({
      title: `AppControlBench: Compare AI Agents on Real iOS Apps${suffix}`,
      description:
        'Compare AI agents on real iOS apps by task completion, cost, speed, model, and tool. ' +
        'Explore the AppControlBench results.',
      path: pagePath(catalog, 'report', edition.id),
      noindex: !isDefault,
      rootId: 'report-root',
      markup: render(h(ReportPage, { initial })),
      payloadId: 'acb-report-initial',
      payload: serializeInlineJson(initial),
      script: report.script,
      styles: report.styles,
    }),
  );

  const explorer = assetsFor(manifest, 'index-runs.html');
  write(
    'index-runs.html',
    documentHtml({
      title: `AppControlBench - Run Explorer${suffix}`,
      description:
        'Browse every AppControlBench run: per-task outcomes, agent transcripts, screenshots, cost ' +
        'and timing for each model and tool pairing on real iOS apps.',
      // cleanUrls (vercel.json) and the nginx try_files fallback both serve this page without the
      // .html suffix, so that is the URL that must be canonical.
      path: pagePath(catalog, 'explorer', edition.id),
      noindex: !isDefault,
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

  if (HAS_TELEMETRY) {
    const telemetry = assetsFor(manifest, 'index-telemetry.html');
    write(
      'index-telemetry.html',
      documentHtml({
        title: `AppControlBench - Telemetry${suffix}`,
        description:
          'Per-call and per-step telemetry for every AppControlBench run: tool call latency, output ' +
          'size, step wall time and token cost for each model and tool pairing.',
        path: pagePath(catalog, 'telemetry', edition.id),
        // Internal, whatever the build says the site is.
        noindex: true,
        bodyClass: 'run-explorer-page',
        rootId: 'telemetry-root',
        // No markup: the screen mounts in the browser (see TELEMETRY_PAGE).
        markup: '',
        payloadId: 'acb-telemetry-bootstrap',
        // The same ReportMeta the report page embeds - it carries the tool catalog, the provenance
        // footer and the manifest - plus the scoring policy, for the baseline tool the context
        // comparison is measured against. Not the whole RunIndex: its 1,080 cells are half a megabyte
        // this screen never reads, and the measurements it does read are the separate fetch below.
        payload: serializeInlineJson({ meta, scoring: runIndex.scoring }),
        script: telemetry.script,
        styles: telemetry.styles,
      }),
    );
  }
  return written;
}

function main(): void {
  const manifest = readJson<ViteManifest>(resolve(PUBLIC, '.vite/manifest.json'));
  // Every edition the export carries gets its own copy of the pages: an edition is a dataset of its
  // own (docs/editions.md), and a static site shows one by serving the page built from it.
  const catalog = readJson<EditionCatalog>(resolve(PUBLIC, 'data/v1/editions.json'));
  requireSchema('editions.json', catalog.schemaVersion);
  if (!catalog.editions.some((edition) => edition.id === catalog.defaultEditionId)) {
    throw new Error(`editions.json opens on ${catalog.defaultEditionId}, which it does not carry.`);
  }
  // Stale archives of editions a previous build carried must not outlive them.
  rmSync(resolve(PUBLIC, 'e'), { recursive: true, force: true });
  const written = catalog.editions.flatMap((edition) => renderEdition(manifest, catalog, edition));

  // Committed static assets, not build products: both must land on stable, unhashed URLs — the social
  // card because it is baked into every og:image tag ever scraped, the favicon because that is the URL
  // the head links. Vite's publicDir is off (vite.config.ts:63 — outDir *is* public/), so the copies
  // happen here; vite.config.ts serves the same files from web/static during dev.
  for (const asset of [OG_IMAGE, FAVICON]) {
    copyFileSync(resolve(ROOT, 'web/static', asset), resolve(PUBLIC, asset));
  }

  // robots.txt + sitemap.xml are generated (not copied) so SITE_URL stays correct for previews.
  // Paths match the cleanUrls canonicals emitted above.
  writeFileSync(resolve(PUBLIC, 'robots.txt'), robotsTxt());
  writeFileSync(
    resolve(PUBLIC, 'sitemap.xml'),
    // Telemetry is deliberately absent: the page is served noindex, and listing a noindex URL here
    // would be the sitemap contradicting the page.
    sitemapXml(['/', '/index-runs']),
  );

  console.log(
    `prerender -> ${catalog.editions.length} edition(s), opening on ${catalog.defaultEditionId}`,
  );
  for (const file of written) {
    const kb = (readFileSync(file).byteLength / 1024).toFixed(1);
    console.log(`prerender -> public/${relative(PUBLIC, file)} (${kb} KB)`);
  }
  console.log(`prerender -> public/robots.txt, public/sitemap.xml (IS_INDEXED=${IS_INDEXED})`);
}

main();
