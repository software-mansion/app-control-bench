// Manifest-aware resource loading with an in-memory cache.
//
// Every path is derived from `BuildManifest.dataRoot` and the run's (modelId, toolId, taskId) rather
// than shipped per row: `RunCell` carries no `detailHref`, which is what keeps 1,800 identical-shaped
// routes out of the payload (docs/report-frontend-contract.md:202).
//
// The cache is keyed by URL and holds the in-flight promise, not the resolved value, so two components
// asking for the same run during the same tick share one request instead of racing.
import type { BuildManifest, RunDetail, RunTranscript } from './contract';
import { runKey } from './contract';

const inFlight = new Map<string, Promise<unknown>>();

export class ResourceError extends Error {
  constructor(
    readonly href: string,
    readonly status: number,
  ) {
    super(`${href} responded ${status}`);
    this.name = 'ResourceError';
  }
}

export function fetchJson<T>(href: string): Promise<T> {
  const hit = inFlight.get(href);
  if (hit) return hit as Promise<T>;
  const request = fetch(href)
    .then((response) => {
      if (!response.ok) throw new ResourceError(href, response.status);
      return response.json() as Promise<T>;
    })
    .catch((error: unknown) => {
      // A failed request must not poison the cache — the next attempt should be able to retry.
      inFlight.delete(href);
      throw error;
    });
  inFlight.set(href, request);
  return request;
}

export function runDetailHref(manifest: BuildManifest, modelId: string, toolId: string, taskId: string): string {
  return `${manifest.dataRoot}/runs/${runKey(modelId, toolId, taskId)}.json`;
}

export function loadRunDetail(
  manifest: BuildManifest,
  modelId: string,
  toolId: string,
  taskId: string,
): Promise<RunDetail> {
  return fetchJson<RunDetail>(runDetailHref(manifest, modelId, toolId, taskId));
}

/**
 * Takes the href off `RunDetail.transcript` rather than deriving it, because a run without a transcript
 * has `href: null` and must show "No transcript recorded" without a request — a missing trace is never
 * a 404 (contract line 247).
 */
export function loadTranscript(href: string): Promise<RunTranscript> {
  return fetchJson<RunTranscript>(href);
}
