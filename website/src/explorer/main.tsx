// Explorer client entry.
//
// In production the matrix is pre-rendered and its RunIndex embedded, so this hydrates — the
// "Loading run explorer..." placeholder report.py shipped is gone. Under `vite dev` there is nothing
// to hydrate, so the index is fetched and the page mounted instead.
import { hydrate, render } from 'preact';

import type { BuildManifest, Provenance, RunIndex } from '../shared/contract';
import { buildExplorerBootstrap, readEmbedded, renderBootstrapError } from '../shared/bootstrap';
import '../styles/index.css';
import { ExplorerPage } from './ExplorerPage';

type Bootstrap = { runIndex: RunIndex; manifest: BuildManifest; provenance: Provenance };

const root = document.getElementById('explorer-root');

if (root) {
  const embedded = readEmbedded<Bootstrap>('acb-explorer-bootstrap');
  if (embedded) {
    hydrate(
      <ExplorerPage runIndex={embedded.runIndex} manifest={embedded.manifest} provenance={embedded.provenance} />,
      root,
    );
  } else {
    buildExplorerBootstrap()
      .then(({ runIndex, manifest, provenance }) =>
        render(<ExplorerPage runIndex={runIndex} manifest={manifest} provenance={provenance} />, root),
      )
      .catch((error: unknown) => renderBootstrapError(root, error));
  }
}
