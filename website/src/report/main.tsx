// Report client entry.
//
// In production the page arrives fully rendered with ReportInitial embedded, so this hydrates the
// existing markup and the default view costs no request. Under `vite dev` there is no prerendered
// markup and no payload, so the same ReportInitial is assembled from the exporter's JSON and mounted.
import { hydrate, render } from 'preact';

import type { ReportInitial } from '../shared/contract';
import { buildReportInitial, readEmbedded, renderBootstrapError } from '../shared/bootstrap';
import '../styles/index.css';
import { ReportPage } from './ReportPage';

const root = document.getElementById('report-root');

if (root) {
  const embedded = readEmbedded<ReportInitial>('acb-report-initial');
  if (embedded) {
    hydrate(<ReportPage initial={embedded} />, root);
  } else {
    buildReportInitial()
      .then((initial) => render(<ReportPage initial={initial} />, root))
      .catch((error: unknown) => renderBootstrapError(root, error));
  }
}
