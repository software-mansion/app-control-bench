// The one report aggregation implementation (docs/report-frontend-contract.md:302-318).
//
// It replaces both halves of what runner/report.py did twice: the Python aggregates (`grade` 194,
// `vdist` 202, `avg_time_success` 214, `avg_price` 229) feeding the server-rendered page, and their
// `TOGGLE_JS` twins (`grade` 1380, `atime` 1381, `acost` 1477) that re-derived the same numbers in the
// browser. The Node prerender and the dev-server fallback now both call this.
//
// Two rules the old pair applied inconsistently are applied uniformly here:
//
//  1. STALE. `grade`, `vdist` and `avg_time_success` all excluded harness-stale runs; `avg_price` did
//     not, and neither did its twin `acost` — so a stale run was kept out of a model's completion and
//     time but left in its cost. (The scatter was inconsistent with *itself*: report.py used `avg_price`
//     and included stale prices, while `TOGGLE_JS.scatterPts` excluded them.) Stale is now excluded
//     everywhere. This moves published cost figures.
//  2. ERROR. `grade` restricted the denominator to `gradedVerdicts`; `avg_price` accepted any non-null
//     verdict including `error`. Contract line 315 excludes `error` because it is a judge failure, not
//     an agent failure. It is excluded everywhere except the verdict distribution, where it must stay
//     visible so a broken judge run cannot hide.
//
// Score weights and the graded-verdict set are read only from `runIndex.scoring`; nothing here knows
// that success is worth 1.0.

import type {
  BarItem,
  BreakdownPanel,
  GradedVerdict,
  LeaderRow,
  MatrixCell,
  MatrixRow,
  Model,
  ReportView,
  RunCell,
  RunIndex,
  ScatterPoint,
  ScoringPolicy,
  Stat,
  Verdict,
} from './contract';

/** Everything the aggregates need beyond the cells themselves. */
type Policy = {
  scoring: ScoringPolicy;
  annulled: ReadonlySet<string>;
};

const NO_STAT: Stat = { value: null, n: 0 };

/**
 * A cell may contribute to an aggregate only when it is neither harness-stale nor on an annulled task.
 * `report.py` spelled this out at four call sites and forgot it at the fifth (`avg_price`); it exists as
 * one function so that cannot happen again.
 */
function eligible(cell: RunCell, policy: Policy): boolean {
  return cell.lifecycle !== 'stale' && !policy.annulled.has(cell.taskId);
}

function isGraded(verdict: Verdict | null, policy: Policy): verdict is GradedVerdict {
  return verdict !== null && (policy.scoring.gradedVerdicts as readonly string[]).includes(verdict);
}

function stat(sum: number, n: number): Stat {
  return n ? { value: sum / n, n } : NO_STAT;
}

/** Mean score over graded runs -> (mean|null, n). `report.py:194`. */
function grade(cells: readonly RunCell[], policy: Policy): Stat {
  let sum = 0;
  let n = 0;
  for (const cell of cells) {
    if (!eligible(cell, policy) || !isGraded(cell.verdict, policy)) continue;
    sum += policy.scoring.weights[cell.verdict] ?? 0;
    n += 1;
  }
  return stat(sum, n);
}

/**
 * Verdict histogram. `report.py:202`. Counts every non-null verdict, `error` included — it is out of the
 * grade denominator but must remain countable, or an API-flaked judge run disappears from the page.
 */
function verdictDistribution(cells: readonly RunCell[], policy: Policy): Record<string, number> {
  const dist: Record<string, number> = { success: 0, partial: 0, fail: 0, error: 0 };
  for (const cell of cells) {
    if (cell.verdict && eligible(cell, policy)) dist[cell.verdict] = (dist[cell.verdict] ?? 0) + 1;
  }
  return dist;
}

/**
 * Mean wall time over SUCCESS runs only — "how long the model takes when it actually succeeds".
 * `report.py:214`. A successful run missing `wallSeconds` still counts, contributing 0, exactly as
 * Python's `r["meta"].get("wall_s") or 0.0` did. (`TOGGLE_JS.atime` dropped such runs from the
 * denominator instead; Python is authoritative.)
 */
function avgTimeSuccess(cells: readonly RunCell[], policy: Policy): Stat {
  let sum = 0;
  let n = 0;
  for (const cell of cells) {
    if (cell.verdict !== 'success' || !eligible(cell, policy)) continue;
    sum += cell.wallSeconds ?? 0;
    n += 1;
  }
  return stat(sum, n);
}

/**
 * Mean run cost in USD over the same run set the completion score uses, so a model's price and its
 * success rate line up. `report.py:229`, plus the two corrections described at the top of this file.
 * Runs with no cost data are skipped; local/free models average to $0.
 */
function avgPrice(cells: readonly RunCell[], policy: Policy): Stat {
  let sum = 0;
  let n = 0;
  for (const cell of cells) {
    if (!eligible(cell, policy) || !isGraded(cell.verdict, policy) || cell.costUsd === null) continue;
    sum += cell.costUsd;
    n += 1;
  }
  return stat(sum, n);
}

/** `[base, members]` in first-appearance order. `report.py:69`, but grouping on the exporter's `base`. */
export function modelGroups(models: readonly Model[]): Array<[string, Model[]]> {
  const groups: Array<[string, Model[]]> = [];
  const index = new Map<string, number>();
  for (const model of models) {
    let at = index.get(model.base);
    if (at === undefined) {
      at = groups.length;
      index.set(model.base, at);
      groups.push([model.base, []]);
    }
    groups[at][1].push(model);
  }
  return groups;
}

function groupBy<T>(items: readonly T[], key: (item: T) => string): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const item of items) {
    const k = key(item);
    const bucket = out.get(k);
    if (bucket) bucket.push(item);
    else out.set(k, [item]);
  }
  return out;
}

/** Fastest/cheapest first, "n/a" last. The order `cost_bars`/`time_bars` published. `report.py:271`. */
function sortBars(items: BarItem[]): BarItem[] {
  return items
    .map((item, i) => ({ item, i }))
    .sort(
      (a, b) =>
        Number(a.item.value === null) - Number(b.item.value === null) ||
        (a.item.value ?? 0) - (b.item.value ?? 0) ||
        a.i - b.i,
    )
    .map((entry) => entry.item);
}

/**
 * Derive every number the report page renders from every eligible model in the catalog.
 */
export function deriveReportView(runIndex: RunIndex): ReportView {
  const { scoring, catalog, cells } = runIndex;
  const policy: Policy = {
    scoring,
    annulled: new Set(catalog.tasks.filter((task) => task.annulled).map((task) => task.id)),
  };

  const models = catalog.models;
  const tools = catalog.tools;
  const toolIds = tools.map((tool) => tool.id);

  const byModel = groupBy(cells, (cell) => cell.modelId);
  const byTool = groupBy(cells, (cell) => cell.toolId);
  const byPair = groupBy(cells, (cell) => `${cell.modelId}\u0000${cell.toolId}`);

  const modelCells = (modelId: string): RunCell[] => byModel.get(modelId) ?? [];
  const toolCells = (toolId: string): RunCell[] => byTool.get(toolId) ?? [];
  const pairCells = (modelId: string, toolId: string): RunCell[] =>
    byPair.get(`${modelId}\u0000${toolId}`) ?? [];
  const familyCells = (members: readonly Model[]): RunCell[] =>
    members.flatMap((member) => modelCells(member.id));

  const overall = grade(cells, policy);
  const dist = verdictDistribution(cells, policy);
  const scoredCount = Object.values(dist).reduce((a, b) => a + b, 0);

  const leaders: LeaderRow[] = [];
  for (const model of models) {
    for (const toolId of toolIds) {
      const pair = pairCells(model.id, toolId);
      const completion = grade(pair, policy);
      if (completion.value === null) continue;
      // The composition bar is drawn from graded verdicts only, so `error` gets no segment.
      const pairDistribution = verdictDistribution(pair, policy);
      leaders.push({
        modelId: model.id,
        toolId,
        completion: completion.value,
        avgSeconds: avgTimeSuccess(pair, policy).value,
        avgPrice: avgPrice(pair, policy).value,
        n: completion.n,
        distribution: {
          success: pairDistribution.success,
          partial: pairDistribution.partial,
          fail: pairDistribution.fail,
        },
      });
    }
  }
  // -completion, then n/a times last, then fastest. Array#sort is stable, so full ties keep model order.
  leaders.sort(
    (a, b) =>
      b.completion - a.completion ||
      Number(a.avgSeconds === null) - Number(b.avgSeconds === null) ||
      (a.avgSeconds ?? 0) - (b.avgSeconds ?? 0),
  );

  const toolScores = toolIds.map((toolId) => ({ toolId, ...grade(toolCells(toolId), policy) }));
  const modelScores = models.map((model) => ({
    modelId: model.id,
    ...grade(modelCells(model.id), policy),
  }));
  const baselineScore = toolScores.find((score) => score.toolId === scoring.baselineToolId);
  const baseline: Stat = baselineScore ? { value: baselineScore.value, n: baselineScore.n } : NO_STAT;

  // `>` / `<` rather than `>=` / `<=`, so a tie keeps the first tool in catalog order the way Python's
  // `max`/`min` do.
  let bestDevice: ({ toolId: string } & Stat) | null = null;
  let bestDeviceValue = -Infinity;
  for (const score of toolScores) {
    if (score.toolId === scoring.baselineToolId || score.value === null) continue;
    if (score.value > bestDeviceValue) {
      bestDevice = score;
      bestDeviceValue = score.value;
    }
  }
  let cheapestTool: ({ toolId: string } & Stat) | null = null;
  let cheapestToolValue = Infinity;
  for (const toolId of toolIds) {
    const price = avgPrice(toolCells(toolId), policy);
    if (price.value === null) continue;
    if (price.value < cheapestToolValue) {
      cheapestTool = { toolId, ...price };
      cheapestToolValue = price.value;
    }
  }

  // report.py:2237 — `hero_max`, the heat denominator: the top per-(model, tool) score.
  let matrixMax = 0;
  for (const model of models) {
    for (const toolId of toolIds) {
      const value = grade(pairCells(model.id, toolId), policy).value ?? 0;
      if (value > matrixMax) matrixMax = value;
    }
  }
  if (matrixMax <= 0) matrixMax = 1;

  const matrixRow = (
    kind: 'parent' | 'child',
    family: string,
    modelId: string | null,
    label: string,
    base: string,
    rowCells: readonly RunCell[],
  ): MatrixRow => {
    const stats = toolIds.map((toolId) =>
      grade(
        rowCells.filter((cell) => cell.toolId === toolId),
        policy,
      ),
    );
    let rowBest: number | null = null;
    for (const s of stats) {
      if (s.value !== null && (rowBest === null || s.value > rowBest)) rowBest = s.value;
    }
    const cells_: MatrixCell[] = stats.map((s) => ({
      value: s.value,
      n: s.n,
      win: s.value !== null && rowBest !== null && rowBest > 0 && s.value === rowBest,
      heat: s.value !== null ? s.value / matrixMax : 0,
    }));
    return { kind, family, modelId, label, base, cells: cells_ };
  };

  // report.py:2252-2263. A family with several effort variants gets an aggregate parent row plus one
  // child row per variant; a lone model is a single parent row. `modelId` is set only on that lone row —
  // it is the only row standing for exactly one model whose label carries a model logo. A multi-variant
  // parent also shows a mark in `report.py`, but that is the FAMILY's mark, and `family` supplies it;
  // child rows (`<th class="rh sub">`) carry no mark at all.
  const rows: MatrixRow[] = [];
  for (const [base, members] of modelGroups(models)) {
    const family = members[0].provider ?? '';
    if (members.length === 1) {
      const model = members[0];
      rows.push(matrixRow('parent', family, model.id, model.label, base, modelCells(model.id)));
      continue;
    }
    rows.push(matrixRow('parent', family, null, base, base, familyCells(members)));
    for (const model of members) {
      rows.push(matrixRow('child', family, null, model.effort ?? '', base, modelCells(model.id)));
    }
  }

  const breakdowns: BreakdownPanel[] = [
    {
      title: 'Cost by model',
      kind: 'cost',
      items: sortBars(
        models.map((model) => ({
          label: model.label,
          ...avgPrice(modelCells(model.id), policy),
          modelId: model.id,
        })),
      ),
      compact: false,
    },
    {
      title: 'Cost by tool',
      kind: 'cost',
      items: sortBars(
        tools.map((tool) => ({
          label: tool.label,
          ...avgPrice(toolCells(tool.id), policy),
          modelId: null,
        })),
      ),
      compact: true,
    },
    {
      title: 'Time by model',
      kind: 'time',
      items: sortBars(
        models.map((model) => ({
          label: model.label,
          ...avgTimeSuccess(modelCells(model.id), policy),
          modelId: model.id,
        })),
      ),
      compact: false,
    },
    {
      title: 'Time by tool',
      kind: 'time',
      items: sortBars(
        tools.map((tool) => ({
          label: tool.label,
          ...avgTimeSuccess(toolCells(tool.id), policy),
          modelId: null,
        })),
      ),
      compact: true,
    },
  ];

  // One point per (model, tool) — a model's argent / agent-device / no-tool runs are separate dots,
  // never collapsed. `n` is the graded count, so it matches the completion the dot is plotted at.
  const scatterPoints = (models: readonly Model[]): ScatterPoint[] =>
    models.flatMap((model) =>
      tools.map((tool) => {
        const pair = pairCells(model.id, tool.id);
        const completion = grade(pair, policy);
        return {
          label: model.label,
          toolId: tool.id,
          price: avgPrice(pair, policy).value,
          completion: completion.value,
          n: completion.n,
          family: model.provider ?? '',
        };
      }),
    );

  return {
    scoringWeights: scoring.weights,
    baselineToolId: scoring.baselineToolId,
    overall,
    scoredCount,
    verdictDistribution: dist,
    leaders,
    best: leaders[0] ?? null,
    toolScores,
    modelScores,
    baseline,
    bestDevice,
    cheapestTool,
    matrix: {
      toolIds,
      rows,
      // Identical by construction to `toolScores`' stats — report.py's <tfoot> (2265-2268) and its
      // `tool_scores` (1973) are the same call. The contract keeps both so the matrix is self-contained.
      overallRow: toolScores.map((score) => ({ value: score.value, n: score.n })),
      max: matrixMax,
    },
    breakdowns,
    scatter: {
      points: scatterPoints(models),
    },
  };
}
