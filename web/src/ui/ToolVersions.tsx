// The tool-version control, and the selection model both pages drive it with.
//
// The catalog publishes every version of every toolkit it has runs for, and `catalog.defaultToolIds`
// names the one per toolkit a reader gets without asking. This module groups that flat variant list by
// toolkit, and turns a selection back into the variant ids the derivation and the matrix filter on.
//
// A toolkit with a single version renders no control: a select that cannot change anything is
// furniture, and the version is already on every column header, leaderboard row and footer line.
import type { Tool } from '../shared/contract';

/** Selection value meaning "every version of this toolkit at once", for a side-by-side comparison. */
export const ALL_VERSIONS = '*';

export type ToolkitGroup = {
  toolkit: string;
  /** The toolkit's display name, shared by its versions. */
  label: string;
  /** Its variants in catalog order: newest version first. */
  variants: Tool[];
  /** The variant shown until the reader picks another. */
  defaultId: string;
};

/** toolkit -> a variant id, or ALL_VERSIONS. A toolkit absent from it sits at its default. */
export type VersionSelection = Record<string, string>;

export function toolkitGroups(
  tools: readonly Tool[],
  defaultToolIds: readonly string[],
): ToolkitGroup[] {
  const defaults = new Set(defaultToolIds);
  const groups: ToolkitGroup[] = [];
  const byToolkit = new Map<string, ToolkitGroup>();
  for (const tool of tools) {
    const group = byToolkit.get(tool.toolkit);
    if (group) {
      group.variants.push(tool);
      continue;
    }
    const fresh: ToolkitGroup = {
      toolkit: tool.toolkit,
      label: tool.label,
      variants: [tool],
      defaultId: tool.id,
    };
    byToolkit.set(tool.toolkit, fresh);
    groups.push(fresh);
  }
  for (const group of groups) {
    // The catalog's own default, or — if this export names none for the toolkit — its newest version.
    group.defaultId = group.variants.find((variant) => defaults.has(variant.id))?.id ?? group.defaultId;
  }
  return groups;
}

export function defaultSelection(groups: readonly ToolkitGroup[]): VersionSelection {
  return Object.fromEntries(groups.map((group) => [group.toolkit, group.defaultId]));
}

/**
 * The variant ids a selection resolves to, in catalog order. A value naming a variant this catalog no
 * longer carries (a stale localStorage entry, a hand-edited URL) falls back to the toolkit's default
 * rather than dropping the toolkit out of the page entirely.
 */
export function visibleToolIds(
  groups: readonly ToolkitGroup[],
  selection: VersionSelection,
): string[] {
  return groups.flatMap((group) => {
    const picked = selection[group.toolkit] ?? group.defaultId;
    if (picked === ALL_VERSIONS) return group.variants.map((variant) => variant.id);
    return group.variants.some((variant) => variant.id === picked) ? [picked] : [group.defaultId];
  });
}

/** Whether any toolkit has a second version to offer — otherwise the control renders nothing. */
export function hasVersionChoice(groups: readonly ToolkitGroup[]): boolean {
  return groups.some((group) => group.variants.length > 1);
}

/** Whether two id sets select the same variants; order is not part of a selection's identity. */
export function sameToolIds(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(b);
  return a.every((id) => set.has(id));
}

function versionOption(variant: Tool): string {
  if (!variant.version) return 'version not recorded';
  return variant.isLatest ? `${variant.version} (latest)` : variant.version;
}

/**
 * The `<label><select>` fields themselves, with no wrapper: each page drops them into its own
 * control row so they inherit that page's palette instead of carrying a second one.
 */
export function ToolVersionFields({
  groups,
  selection,
  onChange,
  onIntent,
}: {
  groups: readonly ToolkitGroup[];
  selection: VersionSelection;
  onChange: (toolkit: string, value: string) => void;
  /** Fired before the change lands, so a page that must fetch to answer can start early. */
  onIntent?: () => void;
}) {
  const choosable = groups.filter((group) => group.variants.length > 1);
  if (!choosable.length) return null;
  return (
    <>
      {choosable.map((group) => (
        <label class="tool-version-field" key={group.toolkit}>
          <span>{group.label} version</span>
          <select
            value={selection[group.toolkit] ?? group.defaultId}
            onFocus={onIntent}
            onPointerEnter={onIntent}
            onChange={(event) => {
              onIntent?.();
              onChange(group.toolkit, event.currentTarget.value);
            }}
          >
            {group.variants.map((variant) => (
              <option key={variant.id} value={variant.id}>
                {versionOption(variant)}
              </option>
            ))}
            <option value={ALL_VERSIONS}>all versions</option>
          </select>
        </label>
      ))}
    </>
  );
}
