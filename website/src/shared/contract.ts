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

export type Tool = {
  id: string;
  label: string;
  version: string | null;
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
  schemaVersion: 2;
  scoring: ScoringPolicy;
  catalog: {
    models: Model[];
    tools: Tool[];
    tasks: Task[];
  };
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
  schemaVersion: 2;
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
  schemaVersion: 2;
  events: TranscriptEvent[];
};

export type BuildManifest = {
  schemaVersion: 2;
  buildId: string;
  dataRoot: string;
  artifactRoot: string;
};

export type Provenance = {
  generatedAt: string;
  judgeLine: string;
  toolVersions: Record<string, string>;
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
  schemaVersion: 2;
  models: Model[];
  tools: Tool[];
  provenance: Provenance;
  methodExamples: MethodExample[];
  manifest: BuildManifest;
};

export type ReportInitial = {
  schemaVersion: 2;
  models: Model[];
  tools: Tool[];
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

export type MatrixCell = Stat & {
  /** True for the best non-zero cell in its row; drives the `.win` class. */
  win: boolean;
  /** value / matrix.max, the raw linear ratio behind the `--hm` custom property. */
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
    overallRow: Stat[];
    /** Top cell value across every eligible model; the denominator for `MatrixCell.heat`. */
    max: number;
  };
  breakdowns: BreakdownPanel[];
  scatter: {
    /** One point for every eligible model and tool. */
    points: ScatterPoint[];
  };
};
