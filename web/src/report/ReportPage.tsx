// The report page. Every number comes from one fixed `ReportView`, so hero, findings, leaderboard,
// matrix, breakdowns and the scatter describe the same set of eligible models.
//
// The markup mirrors runner/report.py:2104-2340 class for class: report.css is unchanged and 794 lines
// of it target this DOM.
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";

import type {
  BarItem,
  BreakdownPanel,
  BuildManifest,
  LeaderRow,
  MatrixCell,
  MatrixRow,
  MethodExample,
  Model,
  ReportInitial,
  ReportView,
  RunIndex,
  StaleStat,
  Tool,
} from "../shared/contract";
import { fmtPrice, fmtTime, pct, pct1, pyRound } from "../shared/format";
import { deriveReportView } from "../shared/metrics";
import { fetchJson } from "../shared/resources";
import { Nav, ReportFooter, SectionHeader } from "../ui/Chrome";
import { EditionField, hasEditionChoice } from "../ui/Editions";
import {
  ToolVersionFields,
  defaultSelection,
  hasVersionChoice,
  sameToolIds,
  toolkitGroups,
  visibleToolIds,
  type VersionSelection,
} from "../ui/ToolVersions";
import { ProviderLogo } from "../ui/ProviderLogo";
import { CostEfficiencyPlot } from "./CostEfficiencyPlot";
import { DitheredGradientBar } from "./DitheredGradientBar";
import { HeroDither } from "./HeroDither";
import swmMark from "../assets/swm-mark-outline-left-top.svg";

type Lookup = {
  model: (id: string) => Model | undefined;
  tool: (id: string) => Tool | undefined;
  toolLabel: (id: string) => string;
};

function useLookup(models: Model[], tools: Tool[]): Lookup {
  return useMemo(() => {
    const byModel = new Map(models.map((m) => [m.id, m]));
    const byTool = new Map(tools.map((t) => [t.id, t]));
    return {
      model: (id) => byModel.get(id),
      tool: (id) => byTool.get(id),
      toolLabel: (id) => byTool.get(id)?.label ?? id,
    };
  }, [models, tools]);
}

// ---------------------------------------------------------------------------- tool versions

type VersionState = {
  groups: ReturnType<typeof toolkitGroups>;
  selection: VersionSelection;
  setVersion: (toolkit: string, value: string) => void;
  /** Start the one fetch a non-default selection needs, before the reader commits to one. */
  prefetch: () => void;
  /** The variants the numbers on screen describe. */
  tools: Tool[];
  view: ReportView;
  status: "ready" | "loading" | "error";
};

/**
 * The default view arrives prerendered, so reading the report costs no request. Only choosing another
 * version needs the full index: it is fetched once, then every later switch is re-derived in the
 * browser by the same `deriveReportView` the prerender ran — one aggregation, two callers, never a
 * second set of numbers.
 */
function useToolVersions(initial: ReportInitial): VersionState {
  const groups = useMemo(
    () => toolkitGroups(initial.tools, initial.defaultToolIds),
    [initial.tools, initial.defaultToolIds],
  );
  const [selection, setSelection] = useState<VersionSelection>(() => defaultSelection(groups));
  const [runIndex, setRunIndex] = useState<RunIndex | null>(null);
  const [failed, setFailed] = useState(false);

  const wanted = useMemo(() => visibleToolIds(groups, selection), [groups, selection]);
  const isDefault = sameToolIds(wanted, initial.defaultToolIds);
  const dataRoot = initial.manifest.dataRoot;

  const prefetch = useCallback(() => {
    if (runIndex || failed) return;
    // fetchJson dedupes by URL, so hovering the control repeatedly costs one request.
    fetchJson<RunIndex>(`${dataRoot}/run-index.json`)
      .then(setRunIndex)
      .catch(() => {
        // Fall back to what is on the page rather than leaving the control claiming a version whose
        // numbers never arrived.
        setSelection(defaultSelection(groups));
        setFailed(true);
      });
  }, [runIndex, failed, dataRoot, groups]);

  useEffect(() => {
    if (!isDefault) prefetch();
  }, [isDefault, prefetch]);

  const resolved = isDefault || !runIndex ? initial.defaultToolIds : wanted;
  const view = useMemo(
    () => (isDefault || !runIndex ? initial.view : deriveReportView(runIndex, wanted)),
    [isDefault, runIndex, wanted, initial.view],
  );
  const tools = useMemo(() => {
    const shown = new Set(resolved);
    return initial.tools.filter((tool) => shown.has(tool.id));
  }, [initial.tools, resolved]);

  return {
    groups,
    selection,
    setVersion: (toolkit, value) => setSelection((prev) => ({ ...prev, [toolkit]: value })),
    prefetch,
    tools,
    view,
    status: failed ? "error" : isDefault || runIndex ? "ready" : "loading",
  };
}

function ToolVersionBar({ state, manifest }: { state: VersionState; manifest: BuildManifest }) {
  if (!hasVersionChoice(state.groups) && !hasEditionChoice(manifest)) return null;
  return (
    <div class="tool-versions" role="group" aria-label="Edition and tool versions">
      <EditionField manifest={manifest} page="report" />
      <ToolVersionFields
        groups={state.groups}
        selection={state.selection}
        onChange={state.setVersion}
        onIntent={state.prefetch}
      />
      {state.status !== "ready" && (
        <span class="tool-versions-note" role="status">
          {state.status === "loading"
            ? "loading runs…"
            : "Could not load the other versions — showing the default."}
        </span>
      )}
    </div>
  );
}

function ModelEffortLabel({ model }: { model: Model }) {
  const effortSuffix = model.effort ? ` (${model.effort})` : "";
  if (!effortSuffix || !model.label.endsWith(effortSuffix)) {
    return <>{model.label}</>;
  }

  return (
    <>
      {model.label.slice(0, -effortSuffix.length)}
      <span class="model-effort">{effortSuffix}</span>
    </>
  );
}

/** The provider mark plus the model's label, as `mlogo(m) + mlabel(m)` produced. */
function ModelName({
  model,
  separateEffort = false,
}: {
  model: Model | undefined;
  separateEffort?: boolean;
}) {
  if (!model) return null;
  return (
    <>
      <ProviderLogo provider={model.provider} />
      {separateEffort ? <ModelEffortLabel model={model} /> : model.label}
    </>
  );
}

// ---------------------------------------------------------------------------- hero + findings

function Hero({
  view,
  lookup,
}: {
  view: ReportView;
  lookup: Lookup;
}) {
  const best = view.best;
  const lead =
    "Compare models, tools, cost, and test runs across real iOS app-control tasks.";
  return (
    <>
      <section
        class="hero-copy benchmark-copy"
        aria-labelledby="benchmark-title"
      >
        <p>
          Created by
          <img
            src={swmMark}
            alt="Software Mansion"
            class="swm-mark"
          />
        </p>
        <h1 id="benchmark-title">AppControlBench</h1>
        <p class="hero-lead">{lead}</p>
      </section>
      <div class="winner-host">
        {best && <BestSummary best={best} lookup={lookup} />}
      </div>
    </>
  );
}

function BestSummary({ best, lookup }: { best: LeaderRow; lookup: Lookup }) {
  const model = lookup.model(best.modelId);
  const version = lookup.tool(best.toolId)?.version;

  return (
    <aside
      class="winner winner-plate winner-plate--signal"
      aria-labelledby="winner-title"
    >
      <div class="winner-plate-surface" aria-hidden="true"></div>
      <div class="winner-plate-topline">
        <h2 id="winner-title">BEST CONFIGURATION</h2>
      </div>
      <div class="winner-plate-result">
        <p>
          <strong>{pyRound(best.completion * 100)}</strong>
          <span>%</span>
          <small>completion</small>
        </p>
        <dl>
          <div>
            <dt>cost / run</dt>
            <dd>{fmtPrice(best.avgPrice)}</dd>
          </div>
          <div>
            <dt>time / run</dt>
            <dd>{fmtTime(best.avgSeconds)}</dd>
          </div>
        </dl>
      </div>
      <div class="winner-plate-pair">
        <div>
          <span>MODEL</span>
          <strong>
            <ModelName model={model} />
          </strong>
        </div>
        <b aria-label="paired with">×</b>
        <div>
          <span>TOOL</span>
          <strong>
            {lookup.toolLabel(best.toolId)}
            {version && <>&nbsp;<small>v{version}</small></>}
          </strong>
        </div>
      </div>
    </aside>
  );
}

// // ---------------------------------------------------------------------------- leaderboard

const GRADED = ["success", "partial", "fail"] as const;

const OUTCOME_LABEL: Record<(typeof GRADED)[number], string> = {
  success: "Passed",
  partial: "Partial",
  fail: "Failed",
};

function OutcomeBreakdown({ row }: { row: LeaderRow }) {
  const outcomes = GRADED.reduce(
    (sum, verdict) => sum + row.distribution[verdict],
    0,
  );
  if (outcomes === 0) return <span class="lb-outcome-na">n/a</span>;

  const summary = GRADED.map(
    (verdict) =>
      `${OUTCOME_LABEL[verdict]} ${pct(row.distribution[verdict] / outcomes)}`,
  );

  const label = `Task outcomes: ${summary.join(", ")}`;
  const tooltipId = `lb-outcomes-${row.modelId}-${row.toolId}`;

  return (
    <div class="lb-outcomes">
      <span
        class="lb-outcome-bar"
        role="img"
        aria-label={label}
        aria-describedby={tooltipId}
        tabIndex={0}
      >
        {GRADED.map((verdict) => {
          const count = row.distribution[verdict];
          return (
            count > 0 && (
              <i
                key={verdict}
                class={`lb-outcome-segment lb-${verdict}`}
                style={{ flex: `${count} 0 0` }}
              />
            )
          );
        })}
      </span>
      <span class="lb-outcome-tooltip" id={tooltipId} role="tooltip">
        {summary.join(", ")}
      </span>
      <span class="lb-outcome-summary" aria-hidden="true">
        {GRADED.map((verdict) => (
          <span key={verdict} class={`lb-outcome-value lb-${verdict}`}>
            <i aria-hidden="true" />
            <b>{pct(row.distribution[verdict] / outcomes)}</b>
            {OUTCOME_LABEL[verdict]}
          </span>
        ))}
      </span>
    </div>
  );
}

function HeroLeaderboard({
  view,
  lookup,
  versions,
  manifest,
}: {
  view: ReportView;
  lookup: Lookup;
  versions: VersionState;
  manifest: BuildManifest;
}) {
  const top = view.leaders.slice(0, 10);
  return (
    <section class="leaderboard-section">
      <div class="hero-leaderboard-intro">
        <div class="benchmark-hero-shell">
          <HeroDither />
          <div class="benchmark-paper-fade" aria-hidden="true" />
          <div class="benchmark-vignette" aria-hidden="true" />
          <div class="benchmark-stage">
            <Nav page="report" manifest={manifest} />
            <Hero view={view} lookup={lookup} />
          </div>
        </div>
        <header class="section-intro" id="leaderboard">
          <div>
            <h2>Leaderboard</h2>
            <p>
              Completion assigns 1 to success, 0.5 to partial, and 0 to failure.
              Ties are broken by mean time on successful runs.
            </p>
          </div>
          {/* Above the first number on the page, because it governs every number below it. */}
          <ToolVersionBar state={versions} manifest={manifest} />
        </header>
      </div>
      <div class="leaderboard-wrap">
        <table class="leaderboard">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Configuration</th>
              <th>Tool</th>
              <th>Completion</th>
              <th>Time / run</th>
              <th>Cost / run</th>
              <th>
                Task outcomes
              </th>
            </tr>
          </thead>
          <tbody>
            {top.map((row, i) => {
              const rank = i + 1;
              const version = lookup.tool(row.toolId)?.version;
              return (
                <tr
                  key={`${row.modelId}__${row.toolId}`}
                  class={rank <= 3 ? "top-rank" : undefined}
                >
                  <td class="rank-pos">{String(rank).padStart(2, "0")}</td>
                  <th scope="row">
                    <span class="rank-model">
                      <ModelName
                        model={lookup.model(row.modelId)}
                        separateEffort
                      />
                      <span class="leaderboard-mobile-tool">
                        · {lookup.toolLabel(row.toolId)}
                        {version && ` v${version}`}
                      </span>
                    </span>
                  </th>
                  <td class="lb-tool">
                    <span class="rank-tool">
                      {lookup.toolLabel(row.toolId)}
                    </span>
                    {version && <small>v{version}</small>}
                  </td>
                  <td class="lb-completion num">{pct1(row.completion)}</td>
                  <td class="lb-time num">{fmtTime(row.avgSeconds)}</td>
                  <td class="lb-cost num">{fmtPrice(row.avgPrice)}</td>
                  <td class="lb-score">
                    <OutcomeBreakdown row={row} />
                    <div class="leaderboard-mobile-metrics">
                      <span>
                        <small>Time / run</small>
                        <b>{fmtTime(row.avgSeconds)}</b>
                      </span>
                      <span>
                        <small>Cost / run</small>
                        <b>{fmtPrice(row.avgPrice)}</b>
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------- matrix

/** "stale - pending re-run", naming the older version when the score was borrowed from one. */
function staleLabel(cell: StaleStat, lookup: Lookup): string {
  if (!cell.staleToolId) return "stale - pending re-run";
  const version = lookup.tool(cell.staleToolId)?.version;
  return `stale (${version ? `v${version}` : "unversioned"}) - pending re-run`;
}

const STALE_TITLE =
  "Nothing current exists here, so this is the newest older result: an earlier harness or tool version. It is excluded from every other score until re-run.";

function MatrixRowCells({ row, lookup }: { row: MatrixRow; lookup: Lookup }) {
  const label =
    row.kind === "child" ? (
      <th class="rh sub">{row.label}</th>
    ) : row.modelId ? (
      <th class="rh">
        <ModelName model={lookup.model(row.modelId)} separateEffort />
      </th>
    ) : (
      <th class="rh">
        <ProviderLogo provider={row.family || null} />
        {row.label}
      </th>
    );
  return (
    <tr
      class={row.kind === "child" ? "grp-child" : "grp-parent"}
      data-family={row.family || undefined}
    >
      {label}
      {row.cells.map((cell, i) =>
        cell.value === null && cell.n === 0 ? (
          <td key={i} class="mc empty">
            pending
          </td>
        ) : cell.stale ? (
          <td key={i} class="mc stale" title={STALE_TITLE}>
            <span class="mp">{pct1(cell.value)}</span>
            <span class="mst">{staleLabel(cell, lookup)}</span>
            <span class="mn">n={cell.n}</span>
          </td>
        ) : (
          // --hm is read by report.css:106,115 to shade the cell; it is a number fed to a theme token,
          // not a raw style value.
          <td
            key={i}
            class={cell.win ? "mc win" : "mc"}
            style={{ "--hm": cell.heat.toFixed(3) }}
          >
            <span class="mp">{pct1(cell.value)}</span>
            <span class="mn">n={cell.n}</span>
          </td>
        ),
      )}
    </tr>
  );
}

type MobileMatrixGroup = {
  id: string;
  label: string;
  family: string;
  cell: MatrixCell;
  variants: Array<{ label: string; cell: MatrixCell }>;
};

/** Current scores rank first, stale ones after them, unscored last. */
function scoreTier(cell: MatrixCell): number {
  return cell.value === null ? 2 : cell.stale ? 1 : 0;
}

function mobileMatrixGroups(
  rows: MatrixRow[],
  toolIndex: number,
): MobileMatrixGroup[] {
  const groups: MobileMatrixGroup[] = [];

  for (const row of rows) {
    const cell = row.cells[toolIndex];
    if (!cell) continue;

    if (row.kind === "parent") {
      groups.push({
        id: `mobile-tool-group-${groups.length}`,
        label: row.base,
        family: row.family,
        cell,
        variants: [],
      });
      continue;
    }

    groups.at(-1)?.variants.push({ label: row.label, cell });
  }

  return groups.sort(
    (a, b) =>
      scoreTier(a.cell) - scoreTier(b.cell) ||
      (b.cell.value ?? -Infinity) - (a.cell.value ?? -Infinity) ||
      a.label.localeCompare(b.label),
  );
}

function MobileToolComparison({
  view,
  lookup,
}: {
  view: ReportView;
  lookup: Lookup;
}) {
  const toolIds = useMemo(
    () =>
      [...view.matrix.toolIds].sort(
        (a, b) =>
          lookup.toolLabel(a).localeCompare(lookup.toolLabel(b)) ||
          a.localeCompare(b),
      ),
    [lookup, view.matrix.toolIds],
  );
  const [pickedToolId, setPickedToolId] = useState(toolIds[0] ?? "");
  const [expandedGroupId, setExpandedGroupId] = useState<string | null>(null);
  // A version switch can drop the picked column from the page.
  const selectedToolId = toolIds.includes(pickedToolId)
    ? pickedToolId
    : (toolIds[0] ?? "");
  const toolIndex = view.matrix.toolIds.indexOf(selectedToolId);
  const overall =
    toolIndex >= 0 ? view.matrix.overallRow[toolIndex] : undefined;
  const groups = useMemo(
    () =>
      toolIndex >= 0 ? mobileMatrixGroups(view.matrix.rows, toolIndex) : [],
    [toolIndex, view.matrix.rows],
  );

  if (!selectedToolId || !overall) return null;

  return (
    <div class="mobile-tool-comparison">
      <label class="mobile-tool-selector">
        <span>Tool</span>
        <select
          value={selectedToolId}
          onChange={(event) => {
            setPickedToolId(event.currentTarget.value);
            setExpandedGroupId(null);
          }}
          aria-label="Select a tool to rank models by completion score"
        >
          {toolIds.map((toolId) => {
            const tool = lookup.tool(toolId);
            const label = tool?.label ?? toolId;
            return (
              <option key={toolId} value={toolId}>
                {tool?.version ? `${label} v${tool.version}` : label}
              </option>
            );
          })}
        </select>
      </label>

      <div class="mobile-tool-overall">
        <span>Average completion score</span>
        <div>
          <strong>{pct1(overall.value)}</strong>
          <small>n={overall.n}</small>
          {overall.stale && (
            <small class="mst">{staleLabel(overall, lookup)}</small>
          )}
        </div>
      </div>

      <ol class="mobile-tool-ranking">
        {groups.map((group, index) => {
          const rowContent = (
            <>
              <span class="mobile-tool-rank">{index + 1}</span>
              <span
                class={
                  group.cell.stale
                    ? "mobile-tool-model stale"
                    : "mobile-tool-model"
                }
              >
                <ProviderLogo provider={group.family || null} />
                {group.label}
                {group.cell.stale && (
                  <small class="mst">{staleLabel(group.cell, lookup)}</small>
                )}
              </span>
              <span
                class={
                  group.cell.stale
                    ? "mobile-tool-score stale"
                    : "mobile-tool-score"
                }
              >
                {pct1(group.cell.value)}
              </span>
            </>
          );

          if (group.variants.length === 0) {
            return (
              <li key={group.id}>
                <div class="mobile-tool-rank-row" style={{ cursor: "default" }}>
                  {rowContent}
                </div>
              </li>
            );
          }

          const expanded = group.id === expandedGroupId;
          const detailId = `mobile-tool-detail-${group.id}`;
          return (
            <li key={group.id}>
              <button
                class="mobile-tool-rank-row"
                type="button"
                aria-expanded={expanded}
                aria-controls={detailId}
                onClick={() => setExpandedGroupId(expanded ? null : group.id)}
              >
                {rowContent}
                <span class="mobile-tool-chevron" aria-hidden="true">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 15 15"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <path
                      d="M3.13523 6.15803C3.3241 5.95657 3.64052 5.94637 3.84197 6.13523L7.5 9.56464L11.158 6.13523C11.3595 5.94637 11.6759 5.95657 11.8648 6.15803C12.0536 6.35949 12.0434 6.67591 11.842 6.86477L7.84197 10.6148C7.64964 10.7951 7.35036 10.7951 7.15803 10.6148L3.15803 6.86477C2.95657 6.67591 2.94637 6.35949 3.13523 6.15803Z"
                      fill="currentColor"
                      fillRule="evenodd"
                      clipRule="evenodd"
                    />
                  </svg>
                </span>
              </button>
              <div
                id={detailId}
                class="mobile-tool-detail"
                hidden={!expanded}
                aria-label={`${group.label} completion score details`}
              >
                <ul>
                  {group.variants.map((variant) => (
                    <li key={variant.label}>
                      <span>{variant.label}</span>
                      <b>{pct1(variant.cell.value)}</b>
                      <small>
                        n={variant.cell.n}
                        {variant.cell.stale &&
                          ` · ${staleLabel(variant.cell, lookup)}`}
                      </small>
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function ComparisonMatrix({
  view,
  lookup,
}: {
  view: ReportView;
  lookup: Lookup;
}) {
  return (
    <section class="compare-section" id="compare">
      <SectionHeader
        title="Tool comparison"
        explain="Completion score by model and tool, partial outcomes at half credit. Every judged run counts, failures included; judge errors do not. Where a version has no current result, the newest older one (an earlier harness or tool version) is shown instead, marked stale - pending re-run."
      />
      <MobileToolComparison view={view} lookup={lookup} />
      <div class="matrix-wrap">
        <section class="hero" id="stat-hero">
          <table class="mt">
            <thead>
              <tr>
                <th class="rh">model</th>
                {view.matrix.toolIds.map((toolId) => {
                  const tool = lookup.tool(toolId);
                  return (
                    <th key={toolId}>
                      {tool?.label ?? toolId}
                      {tool?.version && (
                        <span class="tvh">v{tool.version}</span>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {view.matrix.rows.map((row, i) => (
                <MatrixRowCells key={i} row={row} lookup={lookup} />
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th class="rh ov">overall</th>
                {view.matrix.overallRow.map((cell, i) => (
                  <td
                    key={i}
                    class={cell.stale ? "mc ovc stale" : "mc ovc"}
                    title={cell.stale ? STALE_TITLE : undefined}
                  >
                    <span class="mp">{pct1(cell.value)}</span>
                    {cell.stale && (
                      <span class="mst">{staleLabel(cell, lookup)}</span>
                    )}
                    <span class="mn">n={cell.n}</span>
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </section>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------- breakdowns

/**
 * One bar panel. `time_bars` and `cost_bars` disagree about zero and the renderer must too: a
 * zero-second time is "n/a" and does not scale the bars, a zero cost is "free" and does.
 */
function BarPanel({
  panel,
  lookup,
}: {
  panel: BreakdownPanel;
  lookup: Lookup;
}) {
  const isCost = panel.kind === "cost";
  const shown = (item: BarItem) =>
    isCost ? item.value !== null : Boolean(item.value);
  const scaling = panel.items.filter(shown).map((item) => item.value as number);
  const max = scaling.length ? Math.max(...scaling) : null;
  return (
    <section class={panel.compact ? "panel compact" : "panel"}>
      <h3>{panel.title}</h3>
      <div class="bars">
        {panel.items.map((item) => {
          const model = item.modelId ? lookup.model(item.modelId) : undefined;
          const label = (
            <span class="blab">
              {item.modelId && (
                <ProviderLogo
                  provider={lookup.model(item.modelId)?.provider ?? null}
                />
              )}
              <span class="bname">
                {model ? <ModelEffortLabel model={model} /> : item.label}
              </span>
            </span>
          );
          if (!shown(item)) {
            return (
              <div class="brow" key={item.label}>
                {label}
                <span class="btrack dithered-gradient" />
                <span class="bval na">n/a</span>
              </div>
            );
          }
          const value = item.value as number;
          const width = max ? (value / max) * 100 : 0;
          return (
            <div class="brow" key={item.label}>
              {label}
              <span class="btrack dithered-gradient has-origin">
                <DitheredGradientBar
                  label={`${item.label}: ${isCost ? fmtPrice(value) : fmtTime(value)}`}
                  segments={[{ width: width / 100, color: "--ink" }]}
                />
              </span>
              <span class="bval">
                {isCost ? fmtPrice(value) : fmtTime(value)}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Breakdowns({
  view,
  lookup,
}: {
  view: ReportView;
  lookup: Lookup;
}) {
  return (
    <section class="breakdowns-section" id="breakdowns">
      <SectionHeader
        title="Breakdowns"
        explain="Average time uses successful runs only - failures, partials and timeouts are excluded. Average cost uses the same runs as the completion score, failures included, skipping runs with no price data."
      />

      <div id="stat-breakdowns">
        <div class="grid-sm">
          {view.breakdowns.map((panel) => (
            <BarPanel key={panel.title} panel={panel} lookup={lookup} />
          ))}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------- methodology

/** report.py's method_time — fmtTime, except a missing duration reads as "0s" rather than "n/a". */
function methodTime(seconds: number | null): string {
  return fmtTime(seconds ?? 0);
}

const RAIL = [
  [
    "1",
    "Arrange",
    "A fresh simulator clone and re-seeded app state; the agent gets only the task text and its tool",
  ],
  [
    "2",
    "Act",
    "It drives the device until it decides it is done or the clock runs out",
  ],
  ["3", "Capture", "The final screenshot"],
  [
    "4",
    "Judge",
    "A vision model matches that against the judge screen description",
  ],
] as const;

function titleCase(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

function Methodology({
  examples,
  lookup,
  weights,
}: {
  examples: MethodExample[];
  lookup: Lookup;
  weights: Record<string, number>;
}) {
  const [active, setActive] = useState(0);
  const current = examples[active] ?? examples[0];
  return (
    <section class="methodology-section" id="methodology">
      <header class="section-intro">
        <h2>Methodology</h2>
      </header>
      {examples.length > 0 && (
        <div class="method-walkthrough">
          <div class="method-rail">
            {RAIL.map(([num, label, copy]) => (
              <div key={num}>
                <span class="method-step-head">
                  <i class="method-step-num">{num}</i>
                  <span class="method-step">{label}</span>
                </span>
                <b>{copy}</b>
              </div>
            ))}
          </div>
          <div class="method-example-layout" id="method-examples">
            <nav class="method-task-index" aria-label="Worked task examples">
              {examples.map((example, i) => (
                <button
                  key={example.taskId}
                  type="button"
                  data-example={i}
                  aria-current={i === active ? "true" : "false"}
                  onClick={() => setActive(i)}
                >
                  <span class="method-task-top">
                    <span class="method-eyebrow">
                      {titleCase(example.app)} · {example.action}
                    </span>
                    <span class={`method-chip ${example.verdict}`}>
                      <i />
                      {example.verdict}
                    </span>
                  </span>
                  <span class="method-task-title">{example.title}</span>
                  <span class="method-task-meta">
                    {methodTime(example.wallSeconds)}
                  </span>
                </button>
              ))}
            </nav>
            <div class="method-work" id="method-task-detail">
              {current && (
                <article class={`method-task-detail ${current.verdict}`}>
                  <div class="method-work-hero">
                    <div class="method-hero-copy">
                      <div class="method-detail-meta">
                        <span class="method-eyebrow">{current.taskId}</span>
                        <span>
                          <b>model</b>{" "}
                          {lookup.model(current.modelId)?.label ??
                            current.modelId}
                        </span>
                        <span>
                          <b>tool</b> {lookup.toolLabel(current.toolId)}
                          {lookup.tool(current.toolId)?.version &&
                            ` v${lookup.tool(current.toolId)?.version}`}
                        </span>
                      </div>
                      <p class="method-prompt">{current.prompt}</p>
                      <div class="method-verdict">
                        <span class={`method-chip ${current.verdict}`}>
                          <i />
                          {current.verdict}
                        </span>
                        <span class="method-score">
                          {(weights[current.verdict ?? ""] ?? 0).toFixed(1)}
                        </span>
                        <span class="method-meta">
                          confidence{" "}
                          {current.confidence === null
                            ? "n/a"
                            : current.confidence.toFixed(2).replace(/^0/, "")}
                        </span>
                        <p class="method-reason">{current.reason}</p>
                      </div>
                    </div>
                    <div class="method-hero-shot">
                      {current.screenshotHref ? (
                        <img
                          key={current.taskId}
                          src={current.screenshotHref}
                          alt={`Final screenshot for ${current.taskId}, graded ${current.verdict}`}
                          loading="lazy"
                        />
                      ) : null}
                    </div>
                  </div>
                </article>
              )}
            </div>
          </div>
          <p class="method-limit-note">
            The judge is never told which model produced a screenshot, though
            the action names reveal which tool drove the device.
          </p>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------- page

export function ReportPage({ initial }: { initial: ReportInitial }) {
  // The lookup spans the whole catalog, not just the visible variants: a methodology example names the
  // exact variant that produced it, which may be one the reader has switched away from.
  const lookup = useLookup(initial.models, initial.tools);
  const versions = useToolVersions(initial);
  const view = versions.view;

  return (
    <>
      <main id="top">
        <HeroLeaderboard view={view} lookup={lookup} versions={versions} manifest={initial.manifest} />
        <ComparisonMatrix view={view} lookup={lookup} />
        <Breakdowns view={view} lookup={lookup} />
        <CostEfficiencyPlot view={view} tools={versions.tools} />
        <Methodology
          examples={initial.methodExamples}
          lookup={lookup}
          weights={view.scoringWeights}
        />
      </main>
      <ReportFooter provenance={initial.provenance} />
    </>
  );
}
