import { describe, expect, it } from 'vitest';

import type { Edition } from './contract';
import { editionPrefix, editionSummary, pagePath } from './editions';

const routing = { defaultEditionId: '2026-11' };

describe('edition routing', () => {
  it('serves the default edition at the site root', () => {
    expect(editionPrefix(routing, '2026-11')).toBe('');
    expect(pagePath(routing, 'report', '2026-11')).toBe('/');
    expect(pagePath(routing, 'explorer', '2026-11')).toBe('/index-runs');
    expect(pagePath(routing, 'telemetry', '2026-11')).toBe('/index-telemetry');
  });

  it('serves every other edition under its own prefix', () => {
    expect(editionPrefix(routing, '2026-08')).toBe('/e/2026-08');
    expect(pagePath(routing, 'report', '2026-08')).toBe('/e/2026-08/');
    expect(pagePath(routing, 'explorer', '2026-08')).toBe('/e/2026-08/index-runs');
    expect(pagePath(routing, 'telemetry', '2026-08')).toBe('/e/2026-08/index-telemetry');
  });
});

describe('edition summary', () => {
  const edition: Edition = {
    id: '2026-11',
    label: 'November 2026',
    current: true,
    ranked: true,
    harness: 'isolation-v5',
    opencode: '2.3.0',
    judge: 'gpt-6.1-sol',
    runs: 0,
  };

  it('says what the edition froze', () => {
    expect(editionSummary(edition)).toBe('harness isolation-v5 · OpenCode 2.3.0 · judge gpt-6.1-sol');
  });

  it('says so when its runs predate harness stamps', () => {
    expect(editionSummary({ ...edition, ranked: false, harness: null, judge: null })).toBe(
      'runs from before harness stamps · OpenCode 2.3.0',
    );
  });
});
