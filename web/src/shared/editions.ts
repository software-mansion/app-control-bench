// Where an edition's pages live, and how to get from one edition to another.
//
// An edition is a self-contained dataset (docs/editions.md), and the site is static: every edition is
// prerendered to its own copy of each page. The edition the site opens on is served at the root; any
// other lives under `/e/<id>/`. Switching edition is therefore a navigation, not a re-derivation - the
// page that arrives carries that edition's payload, and nothing on it can mix two editions' runs.
//
// `vite dev` has no prerendered pages, so there the edition travels as `?edition=<id>` and
// shared/bootstrap.ts assembles the payload for it.
import type { BuildManifest, Edition } from './contract';

export type PageName = 'report' | 'explorer' | 'telemetry';

const PAGE_PATH: Record<PageName, string> = {
  report: '/',
  explorer: '/index-runs',
  telemetry: '/index-telemetry',
};

/** The query parameter that names the edition under `vite dev`. */
export const EDITION_PARAM = 'edition';

type Routing = Pick<BuildManifest, 'defaultEditionId'>;

/** `''` for the edition served at the site root, `/e/<id>` for any other. */
export function editionPrefix(manifest: Routing, editionId: string): string {
  return editionId === manifest.defaultEditionId ? '' : `/e/${encodeURIComponent(editionId)}`;
}

/** The production URL of `page` in `editionId`, which is also its canonical path. */
export function pagePath(manifest: Routing, page: PageName, editionId: string): string {
  const path = PAGE_PATH[page];
  const prefix = editionPrefix(manifest, editionId);
  if (!prefix) return path;
  return path === '/' ? `${prefix}/` : `${prefix}${path}`;
}

/** The link to `page` in `editionId` (by default, the edition the current page shows). */
export function pageHref(manifest: BuildManifest, page: PageName, editionId = manifest.edition.id): string {
  if (import.meta.env.DEV) {
    const path = PAGE_PATH[page];
    return editionId === manifest.defaultEditionId
      ? path
      : `${path}?${EDITION_PARAM}=${encodeURIComponent(editionId)}`;
  }
  return pagePath(manifest, page, editionId);
}

/** What an edition froze, for a tooltip. */
export function editionSummary(edition: Edition): string {
  return [
    edition.harness ? `harness ${edition.harness}` : 'runs from before harness stamps',
    edition.opencode ? `OpenCode ${edition.opencode}` : null,
    edition.judge ? `judge ${edition.judge}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}
