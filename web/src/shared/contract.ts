// The data contract between runner/report_data.py and the TypeScript frontend.
//
// Resource types are copied verbatim from docs/report-frontend-contract.md:138-262. Resources carry
// stable IDs and raw values only — never HTML fragments, formatted time/currency strings, or provider
// SVG markup. `nav_category` is intentionally absent (contract line 200): report.py never rendered it,
// so adding it would be new report scope rather than migration work.

export type Verdict = 'success' | 'partial' | 'fail' | 'error';
/** The verdicts that carry a score. `error` is a judge failure, not an agent failure. */
export type GradedVerdict = 'success' | 'partial' | 'fail';
export type Lifecycle = 'pending' | 'completed' | 'judged' | 'stale';

export type RunCell = {
  modelId: string;
  toolId: string;
  taskId: string;
  lifecycle: Lifecycle;
  verdict: Verdict | null;
  wallSeconds: number | null;
  costUsd: number | null;
};

export type Model = {
  id: string;
  label: string;
  /** Provider identity, for colour and logo selection. Distinct from `base`. */
  provider: string | null;
  /** Model-family identity, for parent/child matrix grouping and scatter connector series. */
  base: string;
  effort: string | null;
  /** Preserves the ordering used by matrix children and effort-ladder scatter connectors. */
  effortRank: number;
};

/**
 * One version of one toolkit — the unit the whole matrix is keyed on. `id` is `argent@0.15.0`, or the
 * bare toolkit for a tool that carries no version (the `none` control, or a run recorded before the
 * version stamp existed). A second version of a toolkit is therefore another `Tool`, another column,
 * never a redefinition of the one already published.
 */
export type Tool = {
  id: string;
  /** The toolkit this is a version of: `argent`, `agent-device`, `mobile-mcp`, `none`. Shared by every version. */
  toolkit: string;
  /** The toolkit's display name. Versions of one toolkit share it; `version` is what distinguishes them. */
  label: string;
  version: string | null;
  /** The newest recorded version of its toolkit. Exactly one `Tool` per toolkit carries it. */
  isLatest: boolean;
};

export type Task = {
  id: string;
  app: string;
  kind: string;
  prompt: string;
  annulled: boolean;
};

export type ScoringPolicy = {
  weights: {
    success: number;
    partial: number;
    fail: number;
  };
  gradedVerdicts: Array<'success' | 'partial' | 'fail'>;
  baselineToolId: string;
};

export type RunIndex = {
  schemaVersion: 4;
  scoring: ScoringPolicy;
  catalog: {
    models: Model[];
    /** Every tool variant the export carries, in toolkit order, newest version of each first. */
    tools: Tool[];
    /**
     * The variant per toolkit a reader sees before touching a version control — the newest one,
     * unless `configs/tool-versions.json` names another. Every other entry in `tools` is published
     * and selectable but hidden by default.
     */
    defaultToolIds: string[];
    tasks: Task[];
  };
  /**
   * Full model × task coverage for the default variants, so an unrun task there reads as `pending`.
   * A non-default variant contributes only the runs that exist: a consumer treats a missing cell as
   * pending, so spelling out a whole matrix of them per version would say nothing at real cost.
   */
  cells: RunCell[];
};

/**
 * `RunCell` carries no `detailHref`: detail paths are derived from (modelId, toolId, taskId) under the
 * manifest's versioned `dataRoot`, rather than sending the same derivable route 1,800 times.
 */
export function runKey(modelId: string, toolId: string, taskId: string): string {
  return `${modelId}__${toolId}__${taskId}`;
}

export type RunDetail = {
  schemaVersion: 4;
  run: {
    lifecycle: Lifecycle;
    verdict: Verdict | null;
    confidence: number | null;
    reason: string;
    judgeModel: string | null;
    wallSeconds: number | null;
    costUsd: number | null;
    timedOut: boolean;
    toolCallCount: number;
    toolNames: string[];
    screenshotHref: string | null;
  };
  judgeInput: {
    /**
     * `recorded` when judge.py wrote `judge_input` into score.json; `reconstructed` when the exporter
     * rebuilt the same facts from tasks.json + the run's meta.json (runs scored before judge.py started
     * recording it). The UI surfaces the distinction rather than presenting a guess as evidence.
     */
    source: 'recorded' | 'reconstructed';
    app: string;
    prompt: string;
    solvedScreen: string;
    toolNames: string[];
    toolCallCount: number;
  };
  steps: Array<{
    index: number;
    name: string;
    label: string;
    elapsedSeconds: number | null;
    status: string | null;
  }>;
  /**
   * A run without a transcript has `href: null` and `eventCount: 0`; the UI shows "No transcript
   * recorded" without fetching. Missing trace files must never surface as a 404 error state.
   */
  transcript: {
    href: string | null;
    eventCount: number;
  };
};

export type TranscriptEvent =
  | { k: 't'; x: string; ts: number | null }
  | { k: 'r'; x: string; ts: number | null }
  | { k: 'u'; n: string; i: unknown; o: string; s: string; ts: number | null };

export type RunTranscript = {
  schemaVersion: 4;
  events: TranscriptEvent[];
};

/**
 * One frozen benchmark setup: a harness generation, an OpenCode version, a model roster and a judge.
 * Results are comparable inside an edition and never across two, so the site shows one at a time:
 * each edition is its own exported dataset and its own set of prerendered pages.
 */
export type Edition = {
  id: string;
  label: string;
  /** The edition new runs are being recorded into. */
  current: boolean;
  /** False for a catch-all of runs that predate harness stamps: browsable, never scored. */
  ranked: boolean;
  harness: string | null;
  opencode: string | null;
  judge: string | null;
  runs: number;
};

export type BuildManifest = {
  schemaVersion: 4;
  buildId: string;
  /** This edition's own data and screenshot directories; every resource path derives from them. */
  dataRoot: string;
  artifactRoot: string;
  /** The edition this page shows. */
  edition: Edition;
  /** Every edition the export carries, newest first. */
  editions: Edition[];
  /** The edition served at the site root; every other one lives under `/e/<id>/`. */
  defaultEditionId: string;
};

/** What runner/report_data.py writes to data/v1/editions.json. */
export type EditionCatalog = {
  schemaVersion: 4;
  defaultEditionId: string;
  editions: Edition[];
};

export type Provenance = {
  generatedAt: string;
  judgeLine: string;
  /** "edition August 2026 · harness isolation-v3-progressive · OpenCode 1.4.3". */
  editionLine?: string;
  /** Every version of each toolkit the export carries, newest first. Toolkits with none are absent. */
  toolVersions: Record<string, string[]>;
  appVersions: Record<string, { version?: string; commit?: string; repo?: string }>;
};

export type MethodExample = {
  app: string;
  kind: string;
  title: string;
  action: string;
  taskId: string;
  /** The configuration that produced this example; labels come from ReportInitial.models/tools. */
  modelId: string;
  toolId: string;
  /** The judge's own confidence, shown beside the verdict. */
  confidence: number | null;
  prompt: string;
  solvedScreen: string;
  verdict: Verdict | null;
  reason: string;
  wallSeconds: number | null;
  toolCallCount: number;
  screenshotHref: string | null;
};

/**
 * What runner/report_data.py writes to data/v1/report-meta.json. Deliberately excludes
 * `ReportInitial.view`: Node derives it from the full RunIndex, so Python does not need an
 * aggregation implementation.
 */
export type ReportMeta = {
  schemaVersion: 4;
  models: Model[];
  tools: Tool[];
  defaultToolIds: string[];
  provenance: Provenance;
  methodExamples: MethodExample[];
  manifest: BuildManifest;
};

export type ReportInitial = {
  schemaVersion: 4;
  models: Model[];
  tools: Tool[];
  /** The variants `view` was derived over: what the page shows until the reader picks another version. */
  defaultToolIds: string[];
  view: ReportView;
  provenance: Provenance;
  methodExamples: MethodExample[];
  manifest: BuildManifest;
};

// ---------------------------------------------------------------------------------------------
// ReportView — the output of the one derivation. Every number the report page renders lives here,
// so hero, findings, leaderboard, matrix, breakdowns and scatter describe the same eligible models.
// ---------------------------------------------------------------------------------------------

/** A mean plus the number of runs it was computed over. `value: null` renders as "n/a". */
export type Stat = {
  value: number | null;
  n: number;
};

export type LeaderRow = {
  modelId: string;
  toolId: string;
  completion: number;
  avgSeconds: number | null;
  avgPrice: number | null;
  n: number;
  /**
   * Graded-verdict counts behind `completion`, for the leaderboard's composition bar
   * (`report.py:2196-2205`). Graded only — `error` has no segment, exactly as the bar is drawn today.
   */
  distribution: Record<GradedVerdict, number>;
};

/**
 * A matrix score that may stand on stale runs. `stale` is set only when the column has no
 * current-harness run, so the value is the stale source's score and nothing else; it never blends.
 */
export type StaleStat = Stat & {
  stale: boolean;
  /** The older version of the column's toolkit the stale score came from; null when it is the column's own. */
  staleToolId: string | null;
};

export type MatrixCell = StaleStat & {
  /** True for the best non-zero non-stale cell in its row; drives the `.win` class. */
  win: boolean;
  /** value / matrix.max, the raw linear ratio behind the `--hm` custom property. 0 when stale. */
  heat: number;
};

export type MatrixRow = {
  kind: 'parent' | 'child';
  /** Family colour key: the provider of the group's first member. '' when unknown. */
  family: string;
  /** Present on rows that stand for exactly one model — the label then carries that model's logo. */
  modelId: string | null;
  /** Family name on a multi-variant parent row, effort level on a child row, model label on a lone row. */
  label: string;
  /** The model family name, regardless of row kind — for grouping, never for display. */
  base: string;
  cells: MatrixCell[];
};

export type BarItem = {
  label: string;
  value: number | null;
  n: number;
  /** Set on per-model bars, which show the provider mark beside the name. */
  modelId: string | null;
};

export type BreakdownPanel = {
  title: string;
  /**
   * `time_bars` and `cost_bars` are not symmetric, so the renderer has to know which it is drawing.
   * `time_bars` (report.py:266) treats a falsy value as "n/a" and leaves zeros out of the bar-scaling
   * max; `cost_bars` (290) tests only for null, so a zero renders as "free" and does scale the bars.
   */
  kind: 'cost' | 'time';
  items: BarItem[];
  compact: boolean;
};

export type ScatterPoint = {
  label: string;
  toolId: string;
  price: number | null;
  completion: number | null;
  n: number;
  family: string;
};

export type ReportView = {
  /**
   * Copied off `RunIndex.scoring` so components never need the index itself. The methodology panel
   * prints the weight behind an example's verdict, and the breakdown caption names the baseline tool.
   */
  scoringWeights: ScoringPolicy['weights'];
  baselineToolId: string;
  overall: Stat;
  scoredCount: number;
  verdictDistribution: Record<string, number>;
  leaders: LeaderRow[];
  /** leaders[0], or null when nothing is scored yet. */
  best: LeaderRow | null;
  toolScores: Array<{ toolId: string } & Stat>;
  modelScores: Array<{ modelId: string } & Stat>;
  baseline: Stat;
  /** Highest-scoring non-baseline tool. */
  bestDevice: ({ toolId: string } & Stat) | null;
  cheapestTool: ({ toolId: string } & Stat) | null;
  matrix: {
    toolIds: string[];
    rows: MatrixRow[];
    /** Per-tool footer row, over every eligible model. */
    overallRow: StaleStat[];
    /** Top cell value across every eligible model; the denominator for `MatrixCell.heat`. */
    max: number;
  };
  breakdowns: BreakdownPanel[];
  scatter: {
    /** One point for every eligible model and tool. */
    points: ScatterPoint[];
  };
};

// ---- telemetry (internal only) ------------------------------------------------------------------
//
// What runner/report_data.py writes to data/v1/telemetry.json, and only ever on an internal tree: the
// public environment's `paths: exclude` drops web/index-telemetry.html, the exporter gates on that
// file, and ci/verify-release-tree.sh fails a build whose tree and environment disagree.
//
// Fetched, never embedded. It is ~1 MB over ~1,300 runs against an 11 KB page, which is the same
// trade the transcript migration was about (docs/report-frontend-contract.md:30).

/** How a tool call ended. Indexed by `TelemetryCall[3]`; order is the contract, append only. */
export type CallKind = 'ok' | 'error' | 'aborted' | 'unavailable';

/**
 * One tool call: `[toolNameIndex, durationMs, outputBytes, callKindIndex]`.
 *
 * A tuple against `Telemetry.toolNames` rather than an object, because at ~42k calls the field names
 * outweigh the values. `durationMs` is null where opencode stamped no `state.time`, and what a
 * non-null value measures depends on the tool — see `toolClock()` in shared/telemetry.ts.
 */
export type TelemetryCall = [number, number | null, number, number];

export type TelemetryTokens = {
  input: number;
  output: number;
  reasoning: number;
  cacheRead: number;
  cacheWrite: number;
};

export type TelemetryRun = {
  modelId: string;
  toolId: string;
  taskId: string;
  wallSeconds: number | null;
  costUsd: number | null;
  /**
   * Billable context of the run's first step — input plus both cache halves, before any tool output
   * has accumulated. What the toolkit's own surface costs to put in front of the model, and it is
   * charged again on every step after it.
   */
  firstStepContext: number | null;
  /** Summed over every step of the run. */
  tokens: TelemetryTokens;
  /** Per-step wall time, step_start to step_finish. Toolkit-neutral: it brackets whatever happened. */
  stepWallMs: number[];
  calls: TelemetryCall[];
};

export type Telemetry = {
  schemaVersion: 4;
  callKinds: CallKind[];
  toolNames: string[];
  runs: TelemetryRun[];
};

/**
 * Whether a tool's recorded duration is the thing it appears to be.
 *
 * `direct` — an MCP tool, where `state.time` brackets the real call.
 * `indirect` — `bash` and friends, where opencode stamps `state.time` around bookkeeping *after* the
 *   command returned. Medians 3-4 ms against commands that took seconds, so it is not a duration at
 *   all and must never be summed beside a `direct` one.
 */
export type ToolClock = 'direct' | 'indirect';

/** Per tool name, within one group. */
export type ToolStat = {
  name: string;
  clock: ToolClock;
  calls: number;
  /** Null for an `indirect` clock: there is no honest duration to report. */
  totalMs: number | null;
  medianMs: number | null;
  p90Ms: number | null;
  outputBytes: number;
  /** Counts by kind, in `CallKind` order. */
  kinds: Record<CallKind, number>;
};

/** One model x tool-variant cell, aggregated over its runs. */
export type TelemetryGroup = {
  modelId: string;
  toolId: string;
  runs: number;
  /** Medians over runs, so one 12-minute outlier cannot carry a cell. */
  medianWallSeconds: number | null;
  medianCostUsd: number | null;
  medianFirstStepContext: number | null;
  medianSteps: number | null;
  medianCallsPerRun: number | null;
  medianStepMs: number | null;
  tokensPerRun: TelemetryTokens;
  calls: number;
  kinds: Record<CallKind, number>;
  tools: ToolStat[];
};

export type TelemetryView = {
  groups: TelemetryGroup[];
  /** Every tool variant present, in the order the report orders them. */
  toolIds: string[];
  modelIds: string[];
  /** Tool names seen anywhere, ranked by total calls — the axis the per-tool panels iterate. */
  toolNames: ToolStat[];
  runCount: number;
  callCount: number;
};
