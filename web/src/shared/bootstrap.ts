// Reading the page's embedded payload, with a network fallback for the dev server.
//
// In production each page is prerendered and carries its payload in a <script type="application/json">,
// so the browser hydrates without a request. `vite dev` serves web/index.html as authored — an empty
// root and no payload — so there is nothing to hydrate and the page would render blank. In that case
// the same ReportInitial is assembled in the browser from the exporter's JSON, using the very same
// deriveReportView the prerenderer calls.
import type {
  BuildManifest,
  EditionCatalog,
  Provenance,
  ReportInitial,
  ReportMeta,
  RunIndex,
  ScoringPolicy,
} from './contract';
import { EDITION_PARAM } from './editions';
import { deriveReportView } from './metrics';
import { fetchJson } from './resources';

/** Where the dev server finds the exporter's output. Matches BuildManifest.dataRoot. */
const DEV_DATA_ROOT = '/data/v1';

export function readEmbedded<T>(id: string): T | null {
  const el = document.getElementById(id);
  if (!el?.textContent) return null;
  return JSON.parse(el.textContent) as T;
}

/**
 * The edition named by `?edition=`, else the one the site opens on. Production never gets here: each
 * edition is prerendered to its own page (shared/editions.ts).
 */
async function loadMeta(): Promise<{ runIndex: RunIndex; meta: ReportMeta }> {
  const catalog = await fetchJson<EditionCatalog>(`${DEV_DATA_ROOT}/editions.json`);
  const asked = new URLSearchParams(location.search).get(EDITION_PARAM);
  const edition = catalog.editions.find((item) => item.id === asked)?.id ?? catalog.defaultEditionId;
  const root = `${DEV_DATA_ROOT}/editions/${edition}`;
  const [runIndex, meta] = await Promise.all([
    fetchJson<RunIndex>(`${root}/run-index.json`),
    fetchJson<ReportMeta>(`${root}/report-meta.json`),
  ]);
  return { runIndex, meta };
}

/** Assemble ReportInitial client-side. Identical composition to web/scripts/prerender.mts. */
export async function buildReportInitial(): Promise<ReportInitial> {
  const { runIndex, meta } = await loadMeta();
  return {
    schemaVersion: 4,
    models: meta.models,
    tools: meta.tools,
    defaultToolIds: meta.defaultToolIds,
    view: deriveReportView(runIndex, meta.defaultToolIds),
    provenance: meta.provenance,
    methodExamples: meta.methodExamples,
    manifest: meta.manifest,
  };
}

export async function buildExplorerBootstrap(): Promise<{
  runIndex: RunIndex;
  manifest: BuildManifest;
  provenance: Provenance;
}> {
  const { runIndex, meta } = await loadMeta();
  return { runIndex, manifest: meta.manifest, provenance: meta.provenance };
}

/**
 * The telemetry page's payload. It takes the whole meta rather than a slice of it because the page
 * needs the tool catalog, the provenance footer and the manifest to fetch telemetry.json with. Only
 * the scoring policy is taken off the run index: the screen's numbers come from telemetry.json, so
 * carrying the index's cells here would be half a megabyte it never reads.
 */
export async function buildTelemetryBootstrap(): Promise<{ meta: ReportMeta; scoring: ScoringPolicy }> {
  const { meta, runIndex } = await loadMeta();
  return { meta, scoring: runIndex.scoring };
}

/**
 * The dev server has no data until the exporter has run once, and a bare failed fetch would leave the
 * same blank page this fallback exists to fix. Say what to run instead.
 */
export function renderBootstrapError(root: HTMLElement, error: unknown): void {
  root.textContent = '';
  const box = document.createElement('div');
  box.className = 'rx-error';
  const title = document.createElement('b');
  title.textContent = 'No exported data found.';
  const hint = document.createElement('p');
  hint.textContent =
    'The dev server reads public/data/v1, which the Python exporter writes. Run: npm run dev:data';
  const detail = document.createElement('pre');
  detail.textContent = String(error instanceof Error ? error.message : error);
  box.append(title, hint, detail);
  root.append(box);
}
