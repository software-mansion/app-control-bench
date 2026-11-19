// The edition control: which frozen benchmark setup the page shows.
//
// It shares the tool-version fields' markup and class, so it sits in the same control row on every
// page and inherits that page's styling. Unlike those fields it navigates: each edition is its own
// prerendered page with its own payload (shared/editions.ts), so nothing here re-derives anything.
import type { BuildManifest } from '../shared/contract';
import { editionSummary, pageHref, type PageName } from '../shared/editions';

/** Whether there is a second edition to offer - otherwise the control renders nothing. */
export function hasEditionChoice(manifest: BuildManifest): boolean {
  return manifest.editions.length > 1;
}

export function EditionField({ manifest, page }: { manifest: BuildManifest; page: PageName }) {
  if (!hasEditionChoice(manifest)) return null;
  return (
    <label class="tool-version-field" title={editionSummary(manifest.edition)}>
      <span>Edition</span>
      <select
        value={manifest.edition.id}
        onChange={(event) => {
          const next = event.currentTarget.value;
          if (next !== manifest.edition.id) window.location.assign(pageHref(manifest, page, next));
        }}
      >
        {manifest.editions.map((edition) => (
          <option key={edition.id} value={edition.id} title={editionSummary(edition)}>
            {edition.ranked ? edition.label : `${edition.label} (unranked)`}
          </option>
        ))}
      </select>
    </label>
  );
}
