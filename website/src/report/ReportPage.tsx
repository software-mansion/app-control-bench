// The report page. Every number comes from one fixed `ReportView`, so hero, findings, leaderboard,
// matrix, breakdowns and the scatter describe the same set of eligible models.
//
// The markup mirrors runner/report.py:2104-2340 class for class: report.css is unchanged and 794 lines
// of it target this DOM.
import { useMemo, useState } from "preact/hooks";

import type {
  BarItem,
  BreakdownPanel,
  LeaderRow,
  MatrixCell,
  MatrixRow,
  MethodExample,
  Model,
  Provenance,
  ReportInitial,
  ReportView,
  Tool,
} from "../shared/contract";
import { fmtPrice, fmtTime, pct, pyRound } from "../shared/format";
import { Nav, ReportFooter, SectionHeader } from "../ui/Chrome";
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

/** The provider mark plus the model's label, as `mlogo(m) + mlabel(m)` produced. */
function ModelName({ model }: { model: Model | undefined }) {
  if (!model) return null;
  return (
    <>
      <ProviderLogo provider={model.provider} />
      {model.label}
    </>
  );
}

// ---------------------------------------------------------------------------- hero + findings

function Hero({
  view,
  lookup,
  provenance,
}: {
  view: ReportView;
  lookup: Lookup;
  provenance: Provenance;
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
            alt="Software Mansion logo"
            aria-hidden="true"
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

// // Best first, against the ramp: success is usually the widest segment, so it absorbs the sparse head
// // of the dither while partial and fail land in the dense tail and read as solid amber and red.
const GRADED = ["success", "partial", "fail"] as const;

function HeroLeaderboard({
  view,
  lookup,
  provenance,
}: {
  view: ReportView;
  lookup: Lookup;
  provenance: Provenance;
}) {
  const top = view.leaders.slice(0, 10);
  return (
    <section class="leaderboard-section">
      <div class="hero-leaderboard-intro">
        <div class="benchmark-hero-shell">
          <HeroDither />
          <div class="benchmark-paper-fade" aria-hidden="true" />
          <div class="benchmark-vignette" aria-hidden="true" />
          <Nav page="report" />
          <Hero view={view} lookup={lookup} provenance={provenance} />
        </div>
        <header class="section-intro" id="leaderboard">
          <div>
            <h2>Leaderboard</h2>
            <p>
              Completion assigns 1 to success, 0.5 to partial, and 0 to failure.
              Ties are broken by mean time on successful runs.
            </p>
          </div>
        </header>
      </div>
      <div class="leaderboard-wrap">
        <div class="leaderboard-frame-head">
          <span>
            Top {Math.min(10, view.leaders.length)} of {view.leaders.length}{" "}
            configurations
          </span>
          <span class="leaderboard-legend">
            <i class="lb-success" />
            success
            <i class="lb-partial" />
            partial
            <i class="lb-fail" />
            fail
          </span>
        </div>
        <table class="leaderboard">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Configuration</th>
              <th>Tool</th>
              <th>Completion</th>
              <th>Time / run</th>
              <th>Cost / run</th>
              <th>Composition</th>
            </tr>
          </thead>
          <tbody>
            {top.map((row, i) => {
              const rank = i + 1;
              const version = lookup.tool(row.toolId)?.version;
              const outcomes = GRADED.reduce(
                (sum, v) => sum + row.distribution[v],
                0,
              );
              return (
                <tr
                  key={`${row.modelId}__${row.toolId}`}
                  class={rank <= 3 ? "top-rank" : undefined}
                >
                  <td class="rank-pos">{String(rank).padStart(2, "0")}</td>
                  <th scope="row">
                    <span class="rank-model">
                      <ModelName model={lookup.model(row.modelId)} />
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
                  <td class="lb-completion num">{pct(row.completion)}</td>
                  <td class="lb-time num">{fmtTime(row.avgSeconds)}</td>
                  <td class="lb-cost num">{fmtPrice(row.avgPrice)}</td>
                  <td
                    class="lb-score"
                    aria-label={`Outcome composition for ${pct(row.completion)} completion`}
                  >
                    {outcomes > 0 && (
                      <span class="lb-composition">
                        <DitheredGradientBar
                          label={`Outcome composition for ${pct(row.completion)} completion`}
                          segments={GRADED.map((verdict) => ({
                            width: row.distribution[verdict] / outcomes,
                            color:
                              verdict === "success"
                                ? "--ok"
                                : verdict === "partial"
                                  ? "--warn"
                                  : "--bad",
                          }))}
                        />
                      </span>
                    )}
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

function MatrixRowCells({ row, lookup }: { row: MatrixRow; lookup: Lookup }) {
  const label =
    row.kind === "child" ? (
      <th class="rh sub">{row.label}</th>
    ) : row.modelId ? (
      <th class="rh">
        <ModelName model={lookup.model(row.modelId)} />
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
        ) : (
          // --hm is read by report.css:106,115 to shade the cell; it is a number fed to a theme token,
          // not a raw style value.
          <td
            key={i}
            class={cell.win ? "mc win" : "mc"}
            style={{ "--hm": cell.heat.toFixed(3) }}
          >
            <span class="mp">{pct(cell.value)}</span>
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
  const [selectedToolId, setSelectedToolId] = useState(toolIds[0] ?? "");
  const [expandedGroupId, setExpandedGroupId] = useState<string | null>(null);
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
        <span>Completion score</span>
        <select
          value={selectedToolId}
          onChange={(event) => {
            setSelectedToolId(event.currentTarget.value);
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
        <span>Overall</span>
        <div>
          <strong>{pct(overall.value)}</strong>
          <small> n={overall.n}</small>
        </div>
      </div>

      <ol class="mobile-tool-ranking">
        {groups.map((group, index) => {
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
                <span class="mobile-tool-rank">{index + 1}</span>
                <span class="mobile-tool-model">
                  <ProviderLogo provider={group.family || null} />
                  {group.label}
                </span>
                <span
                  class="mobile-tool-score"
                  style={{ "--hm": group.cell.heat.toFixed(3) }}
                >
                  {pct(group.cell.value)}
                </span>
                <span class="mobile-tool-chevron" aria-hidden="true">
                  ⌄
                </span>
              </button>
              <div
                id={detailId}
                class="mobile-tool-detail"
                hidden={!expanded}
                aria-label={`${group.label} completion score details`}
              >
                {group.variants.length > 0 && (
                  <ul>
                    {group.variants.map((variant) => (
                      <li key={variant.label}>
                        <span>{variant.label}</span>
                        <b>{pct(variant.cell.value)}</b>
                        <small>n={variant.cell.n}</small>
                      </li>
                    ))}
                  </ul>
                )}
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
      <SectionHeader title="Tool comparison" />
      <MobileToolComparison view={view} lookup={lookup} />
      <div class="matrix-wrap">
        <section class="hero" id="stat-hero">
          <div class="hlabel">completion score by model and tool</div>
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
                  <td key={i} class="mc ovc">
                    <span class="mp">{pct(cell.value)}</span>
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
          const label = (
            <span class="blab">
              {item.modelId && (
                <ProviderLogo
                  provider={lookup.model(item.modelId)?.provider ?? null}
                />
              )}
              <span class="bname">{item.label}</span>
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
                <i> n{item.n}</i>
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
        explain="Time uses successful runs only."
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

/** report.py's method_time — note the space before the seconds, which fmtTime does not have. */
function methodTime(seconds: number | null): string {
  const t = pyRound(seconds ?? 0);
  return t < 60
    ? `${t}s`
    : `${Math.floor(t / 60)}m ${String(t % 60).padStart(2, "0")}s`;
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
            the action names reveal which tool drove the device. It cannot see
            intermediate screens, so a run that reaches the right end state by a
            wrong route still scores as a success. That is the main known
            weakness of final-state grading.
          </p>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------- page

export function ReportPage({ initial }: { initial: ReportInitial }) {
  const lookup = useLookup(initial.models, initial.tools);

  return (
    <>
      <main id="top">
        <HeroLeaderboard
          view={initial.view}
          lookup={lookup}
          provenance={initial.provenance}
        />
        <ComparisonMatrix view={initial.view} lookup={lookup} />
        <Breakdowns view={initial.view} lookup={lookup} />
        <CostEfficiencyPlot view={initial.view} tools={initial.tools} />
        <Methodology
          examples={initial.methodExamples}
          lookup={lookup}
          weights={initial.view.scoringWeights}
        />
      </main>
      <ReportFooter provenance={initial.provenance} />
    </>
  );
}
