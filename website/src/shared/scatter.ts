// Cost-efficiency scatter geometry: x = avg price per task, y = completion score. One mark per
// MODEL x TOOL — a model's argent / agent-device / no-tool runs are separate marks (shape = tool,
// colour = model family), never collapsed — joined by a connector along a model's thinking-effort
// ladder on one tool. The sweet spot is the top-left corner: cheap and high-completion.
//
// The one port of runner/report.py's `_nice_ticks` (246), `TOOL_MARK`/`_marker` (380-392),
// `_fam_variants` (395), `_fam_cls` (411), `_box_overlap` (419), `_seg_in_box` (426), `_place_labels`
// (447), `_leader` (474), `_scatter_layer` (490), `_scatter_legend` (560) and `scatter_svg` (585),
// replacing those and their `TOGGLE_JS` twins (1501-1686).
//
// Unlike both originals this module returns DATA, not markup: positions, path `d` strings, class names,
// tick values and label placements that the report's TSX renders as JSX. Every `data-*` attribute the
// interaction layer reads (`data-pair`, `data-model`, `data-tool`, `data-scatter-tip`, `data-cx`,
// `data-cy`) survives as a field, so hover tooltips, the SVG crosshair and click-to-filter legend
// selection keep working against the same hooks.
//
// No colour is written anywhere here. A mark's hue rides on a `fam-*` class styled in runner/report.css,
// which is the same class its connector, leader, label and legend swatch carry — one model is one colour
// everywhere on the page.

import type { ScatterPoint, Tool } from './contract';
import { fmtCoord, fmtPrice, pct } from './format';

export type ScatterLayout = 'desktop' | 'compact' | 'phone';

type ScatterCanvas = {
  width: number;
  height: number;
  margin: { left: number; right: number; top: number; bottom: number };
};

// The desktop canvas is verbatim from report.py:596. Smaller canvases redraw the same data at a
// readable scale instead of shrinking the 920px desktop SVG until its annotations disappear.
const SCATTER_CANVASES: Record<ScatterLayout, ScatterCanvas> = {
  desktop: { width: 920, height: 560, margin: { left: 76, right: 54, top: 38, bottom: 78 } },
  compact: { width: 640, height: 640, margin: { left: 68, right: 26, top: 38, bottom: 78 } },
  phone: { width: 400, height: 520, margin: { left: 56, right: 16, top: 30, bottom: 64 } },
};

/** Vertical offset from a label's box centre to its SVG text baseline (report.py:555). */
const LABEL_BASELINE = 3.6;

export type MarkerShape = 'circle' | 'square' | 'diamond';

/** The mark SHAPE carries the tool; anything unmapped falls back to a circle. report.py:380. */
export const TOOL_MARK: Record<string, MarkerShape> = {
  argent: 'circle',
  'agent-device': 'square',
  none: 'diamond',
};

export type MarkerGeometry =
  | { shape: 'square'; x: number; y: number; width: number; height: number; rx: number }
  | { shape: 'diamond'; d: string }
  | { shape: 'circle'; cx: number; cy: number; r: number };

/** One scatter mark's geometry. report.py:383. */
export function marker(shape: MarkerShape, cx: number, cy: number): MarkerGeometry {
  if (shape === 'square') {
    return { shape, x: cx - 4.3, y: cy - 4.3, width: 8.6, height: 8.6, rx: 1 };
  }
  if (shape === 'diamond') {
    const d =
      'M' + fmtCoord(cx) + ' ' + fmtCoord(cy - 6.2) +
      'L' + fmtCoord(cx + 6.2) + ' ' + fmtCoord(cy) +
      'L' + fmtCoord(cx) + ' ' + fmtCoord(cy + 6.2) +
      'L' + fmtCoord(cx - 6.2) + ' ' + fmtCoord(cy) + 'Z';
    return { shape, d };
  }
  return { shape: 'circle', cx, cy, r: 5 };
}

// --- label-level identity -----------------------------------------------------------------------
// The scatter groups and labels marks by DISPLAY LABEL, so these take a label rather than a model id.
// `RunIndex.catalog.models` carries `base`/`effortRank` for exactly this, but `ScatterPoint` (contract
// line 241) carries only `label`, so the split is re-derived here as both originals did.

/** `"gpt-5.4-mini (high)"` -> `"gpt-5.4-mini"`. report.py:60. */
export function baseOf(label: string): string {
  const at = label.indexOf(' (');
  return (at < 0 ? label : label.slice(0, at)).trim();
}

/** `"gpt-5.4-mini (high)"` -> `"high"`, `""` when there is no thinking suffix. report.py:61. */
export function thinkOf(label: string): string {
  const open = label.indexOf('(');
  const close = label.lastIndexOf(')');
  return open >= 0 && close > open ? label.slice(open + 1, close).trim() : '';
}

const EFF_RANK: Record<string, number> = {
  'no think': 0,
  'no-think': 0,
  none: 0,
  low: 1,
  med: 2,
  medium: 2,
  high: 3,
  xhigh: 4,
};

/** Order of a model's effort ladder; unknown levels sort last. report.py:65. */
export function effRank(label: string): number {
  return EFF_RANK[thinkOf(label).toLowerCase()] ?? 99;
}

// --- family colour variants ---------------------------------------------------------------------

const famKey = (family: string, base: string): string => family + '|' + base;

/**
 * First-appearance index of each family's base models -> 0 | 1 | 2. v0 is the family's own hue; v1/v2
 * step lightness within it, so a second or third model sharing a family stays distinguishable. A fourth
 * model in one family reuses v2. report.py:395.
 */
export function famVariants(points: readonly ScatterPoint[]): Map<string, number> {
  const order = new Map<string, string[]>();
  for (const point of points) {
    if (!point.family) continue;
    const base = baseOf(point.label);
    let bases = order.get(point.family);
    if (!bases) {
      bases = [];
      order.set(point.family, bases);
    }
    if (!bases.includes(base)) bases.push(base);
  }
  const out = new Map<string, number>();
  for (const [family, bases] of order) {
    bases.forEach((base, i) => out.set(famKey(family, base), Math.min(i, 2)));
  }
  return out;
}

/** CSS class carrying a mark's colour: `fam-<x>[ v1|v2]`, or `''` with no known family. report.py:411. */
export function famCls(family: string, base: string, variants: Map<string, number>): string {
  if (!family) return '';
  const variant = variants.get(famKey(family, base)) ?? 0;
  return 'fam-' + family + (variant ? ' v' + variant : '');
}

// --- label placement ----------------------------------------------------------------------------

/** A centre/width/height box. */
export type Box = { x: number; y: number; w: number; h: number };

/** A connector segment, `[x1, y1, x2, y2]`. */
export type Segment = readonly [number, number, number, number];

/** Overlapping area of two boxes, 0 when they are clear of each other. report.py:419. */
export function boxOverlap(a: Box, b: Box): number {
  const ox = Math.min(a.x + a.w / 2, b.x + b.w / 2) - Math.max(a.x - a.w / 2, b.x - b.w / 2);
  const oy = Math.min(a.y + a.h / 2, b.y + b.h / 2) - Math.max(a.y - a.h / 2, b.y - b.h / 2);
  return ox > 0 && oy > 0 ? ox * oy : 0;
}

/**
 * Length of a segment that falls inside `box` — a Liang-Barsky clip, 0 when it misses. This is what
 * lets a label avoid lying across a connector, which pairwise mark separation cannot see. report.py:426.
 */
export function segInBox(seg: Segment, box: Box): number {
  const dx = seg[2] - seg[0];
  const dy = seg[3] - seg[1];
  let lo = 0;
  let hi = 1;
  const tests: Array<[number, number]> = [
    [-dx, seg[0] - (box.x - box.w / 2)],
    [dx, box.x + box.w / 2 - seg[0]],
    [-dy, seg[1] - (box.y - box.h / 2)],
    [dy, box.y + box.h / 2 - seg[1]],
  ];
  for (const [p, q] of tests) {
    if (p === 0) {
      if (q < 0) return 0;
      continue;
    }
    const t = q / p;
    if (p < 0) lo = Math.max(lo, t);
    else hi = Math.min(hi, t);
    if (lo > hi) return 0;
  }
  return Math.hypot(dx, dy) * (hi - lo);
}

const CAND_ANGLES = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330];
const CAND_RADII = [26, 42, 62, 88, 120];

/** A label box with the anchor it names. `x`/`y` are seeded at the anchor and written back by `placeLabels`. */
export type LabelBox = {
  text: string;
  /** Anchor: the centroid of the series this label names. */
  ax: number;
  ay: number;
  /** Box centre. */
  x: number;
  y: number;
  w: number;
  h: number;
};

/**
 * Callout placement: each label tries a ring of candidate offsets around its own anchor and keeps the
 * cheapest — cost counts area overlapped with marks and with already-placed labels, the length of any
 * connector crossing it, and a mild pull back toward the anchor so a label stays beside the series it
 * names. Widest labels go first, being hardest to fit. Whatever distance is left over gets bridged by
 * `leader`, so a label parked in open space still reads as belonging to its own marks. report.py:447.
 *
 * Deterministic: the candidate ring, the traversal order and the cost weights are all fixed, so the
 * same input always yields the same placement. Mutates `labels`.
 */
export function placeLabels(
  labels: LabelBox[],
  dots: ReadonlyArray<{ cx: number; cy: number }>,
  segs: readonly Segment[],
  xmin: number,
  xmax: number,
  ymin: number,
  ymax: number,
): void {
  const markers: Box[] = dots.map((dot) => ({ x: dot.cx, y: dot.cy, w: 14, h: 14 }));
  const placed: Box[] = [];
  for (const label of [...labels].sort((a, b) => b.w - a.w)) {
    let best: Box | null = null;
    let bestCost = Infinity;
    for (const r of CAND_RADII) {
      for (const angle of CAND_ANGLES) {
        const rad = (angle * Math.PI) / 180;
        const cand: Box = {
          x: Math.min(Math.max(label.ax + Math.cos(rad) * r, xmin + label.w / 2), xmax - label.w / 2),
          y: Math.min(Math.max(label.ay + Math.sin(rad) * r, ymin + label.h / 2), ymax - label.h / 2),
          w: label.w,
          h: label.h,
        };
        let cost = r * 0.45;
        for (const m of markers) cost += boxOverlap(cand, m) * 0.9;
        for (const p of placed) cost += boxOverlap(cand, p) * 1.6;
        for (const s of segs) cost += segInBox(s, cand) * 6.0;
        if (best === null || cost < bestCost) {
          best = cand;
          bestCost = cost;
        }
      }
    }
    // `best` is always set: CAND_RADII x CAND_ANGLES is never empty.
    if (!best) continue;
    label.x = best.x;
    label.y = best.y;
    placed.push(best);
  }
}

/**
 * Leader segment from a label's anchor to its box edge, or null when the label already sits on its own
 * series and the bridge would be noise. Starts clear of the mark and stops just short of the text.
 * report.py:474.
 */
export function leader(label: LabelBox): [number, number, number, number] | null {
  const dx = label.x - label.ax;
  const dy = label.y - label.ay;
  const dist = Math.hypot(dx, dy);
  if (dist < 1e-6) return null;
  const ux = dx / dist;
  const uy = dy / dist;
  const edge = Math.min(
    ux ? (label.w / 2 + 3) / Math.abs(ux) : Infinity,
    uy ? (label.h / 2 + 3) / Math.abs(uy) : Infinity,
  );
  const start = 7.0;
  const end = dist - edge;
  if (end - start < 4) return null;
  return [label.ax + ux * start, label.ay + uy * start, label.ax + ux * end, label.ay + uy * end];
}

// --- the drawable layer -------------------------------------------------------------------------

/** The three `data-*` attributes that identify a selectable base-model x tool series. */
export type PairRef = {
  /** `data-pair` */
  pair: string;
  /** `data-model` — the family BASE, not a model id: one series spans a model's whole effort ladder. */
  model: string;
  /** `data-tool` */
  tool: string;
};

export type Point = { x: number; y: number };

export type ScatterConnector = PairRef & {
  className: string;
  /** The wider transparent hit target drawn over the connector; it carries the role/tabindex. */
  hitClassName: string;
  points: Point[];
  /** `points` pre-joined at the precision report.py wrote. */
  pointsAttr: string;
  ariaLabel: string;
};

export type ScatterLeader = PairRef & {
  className: string;
  points: [Point, Point];
  pointsAttr: string;
};

export type ScatterMarker = PairRef & {
  className: string;
  geometry: MarkerGeometry;
  /** `data-cx` — the crosshair's x anchor. */
  cx: number;
  /** `data-cy` — the crosshair's y anchor. */
  cy: number;
  /** `data-scatter-tip` — price / completion / n, which the callout label does not show. */
  tip: string;
  ariaLabel: string;
};

export type ScatterLabel = PairRef & {
  className: string;
  text: string;
  /** Box centre. */
  x: number;
  y: number;
  /** SVG text baseline. */
  textY: number;
};

/** A point resolved to plot coordinates. */
export type ScatterDot = {
  label: string;
  toolId: string;
  price: number;
  completion: number;
  n: number;
  family: string;
  cx: number;
  cy: number;
};

export type ScatterLayer = {
  connectors: ScatterConnector[];
  leaders: ScatterLeader[];
  markers: ScatterMarker[];
  labels: ScatterLabel[];
};

const pointsAttr = (points: readonly Point[]): string =>
  points.map((p) => fmtCoord(p.x) + ',' + fmtCoord(p.y)).join(' ');

/**
 * Connectors, marks and one callout label per (base model x tool) series, for a set of already
 * positioned dots. A lone dot gets no connector. report.py:490.
 */
export function scatterLayer(
  dots: readonly ScatterDot[],
  variants: Map<string, number>,
  toolLabel: (toolId: string) => string,
  canvas: ScatterCanvas,
): ScatterLayer {
  const groups = new Map<string, ScatterDot[]>();
  const order: string[] = [];
  for (const dot of dots) {
    const key = baseOf(dot.label) + '|' + dot.toolId;
    let group = groups.get(key);
    if (!group) {
      group = [];
      groups.set(key, group);
      order.push(key);
    }
    group.push(dot);
  }

  const connectors: ScatterConnector[] = [];
  const segs: Segment[] = [];
  const drafts: Array<LabelBox & { className: string; ref: PairRef }> = [];

  for (const key of order) {
    const members = groups.get(key) ?? [];
    const ordered = [...members].sort(
      (a, b) => effRank(a.label) - effRank(b.label) || a.cx - b.cx,
    );
    const first = ordered[0];
    const base = baseOf(first.label);
    const toolId = first.toolId;
    const className = famCls(first.family, base, variants);
    const ref: PairRef = { pair: base + '|' + toolId, model: base, tool: toolId };
    const ariaLabel = 'Select ' + base + ' × ' + toolLabel(toolId);

    let ax: number;
    let ay: number;
    if (ordered.length >= 2) {
      const points = ordered.map((m) => ({ x: m.cx, y: m.cy }));
      connectors.push({
        ...ref,
        className: className ? 'ptline ' + className : 'ptline',
        hitClassName: 'ptline-hit',
        points,
        pointsAttr: pointsAttr(points),
        // No tip: the callout label already names the series, so a hover tip here would only echo it.
        ariaLabel,
      });
      for (let i = 1; i < ordered.length; i += 1) {
        segs.push([ordered[i - 1].cx, ordered[i - 1].cy, ordered[i].cx, ordered[i].cy]);
      }
      ax = ordered.reduce((sum, m) => sum + m.cx, 0) / ordered.length;
      ay = ordered.reduce((sum, m) => sum + m.cy, 0) / ordered.length;
    } else {
      ax = first.cx;
      ay = first.cy;
    }

    const text = base + ' × ' + toolLabel(toolId);
    drafts.push({
      text,
      ax,
      ay,
      x: ax,
      y: ay,
      w: text.length * 6.6 + 8,
      h: 13,
      className,
      ref,
    });
  }

  placeLabels(
    drafts,
    dots,
    segs,
    canvas.margin.left + 4,
    canvas.width - canvas.margin.right - 4,
    canvas.margin.top + 8,
    canvas.height - canvas.margin.bottom - 8,
  );

  // Leaders sit under the marks they point at, so they are emitted before the marks.
  const leaders: ScatterLeader[] = [];
  for (const draft of drafts) {
    const line = leader(draft);
    if (!line) continue;
    const points: [Point, Point] = [
      { x: line[0], y: line[1] },
      { x: line[2], y: line[3] },
    ];
    leaders.push({
      ...draft.ref,
      className: draft.className ? 'ptlead ' + draft.className : 'ptlead',
      points,
      pointsAttr: pointsAttr(points),
    });
  }

  const markers: ScatterMarker[] = dots.map((dot) => {
    const base = baseOf(dot.label);
    const className = famCls(dot.family, base, variants);
    const label = toolLabel(dot.toolId);
    return {
      pair: base + '|' + dot.toolId,
      model: base,
      tool: dot.toolId,
      className: className ? 'pt ' + className : 'pt',
      geometry: marker(TOOL_MARK[dot.toolId] ?? 'circle', dot.cx, dot.cy),
      cx: dot.cx,
      cy: dot.cy,
      tip:
        dot.label + ' / ' + label + '; ' + fmtPrice(dot.price) + ' per task; ' +
        pct(dot.completion) + ' completion; n=' + dot.n,
      ariaLabel: 'Select ' + base + ' × ' + label,
    };
  });

  const labels: ScatterLabel[] = drafts.map((draft) => ({
    ...draft.ref,
    className: draft.className ? 'ptlabel ' + draft.className : 'ptlabel',
    text: draft.text,
    x: draft.x,
    y: draft.y,
    textY: draft.y + LABEL_BASELINE,
  }));

  return { connectors, leaders, markers, labels };
}

// --- axis, legend and assembly ------------------------------------------------------------------

/** ~n round-number ticks from 0 up to at least `mx`; the last tick is the axis max. report.py:246. */
export function niceTicks(mx: number, n = 5): number[] {
  if (mx <= 0) return [0, 1];
  const raw = mx / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= raw) ?? 10 * mag;
  const round6 = (t: number): number => Math.round(t * 1e6) / 1e6;
  const ticks: number[] = [];
  let t = 0;
  while (t < mx) {
    ticks.push(round6(t));
    t += step;
  }
  ticks.push(round6(t));
  return ticks;
}

/** A point that has both coordinates and can therefore be drawn. */
type PlottedPoint = ScatterPoint & { price: number; completion: number };

const isPlotted = (point: ScatterPoint): point is PlottedPoint =>
  point.price !== null && point.completion !== null;

/** Whether the efficiency section has anything to show. report.py:2301. */
export function hasScatterData(points: readonly ScatterPoint[]): boolean {
  return points.some((point) => point.price !== null);
}

export type ScatterLegend = {
  models: Array<{ base: string; family: string; swatchClassName: string }>;
  tools: Array<{ toolId: string; label: string; shape: MarkerShape; markClassName: string }>;
};

/**
 * Selectable legend, kept outside the plot. The model swatch is not decoration: the plot encodes MODEL
 * as the family hue, so without it the legend names the series without saying which marks are which.
 * report.py:560.
 */
export function scatterLegend(
  points: readonly ScatterPoint[],
  tools: readonly Tool[],
): ScatterLegend {
  const plotted = points.filter(isPlotted);
  const bases: string[] = [];
  const familyOfBase = new Map<string, string>();
  for (const point of plotted) {
    const base = baseOf(point.label);
    if (!bases.includes(base)) bases.push(base);
    if (!familyOfBase.get(base)) familyOfBase.set(base, point.family || 'none');
  }
  return {
    models: bases.map((base) => {
      const family = familyOfBase.get(base) || 'none';
      return { base, family, swatchClassName: 'legend-swatch fam-' + family };
    }),
    tools: tools
      .filter((tool) => plotted.some((point) => point.toolId === tool.id))
      .map((tool) => {
        const shape = TOOL_MARK[tool.id] ?? 'circle';
        return {
          toolId: tool.id,
          label: tool.label,
          shape,
          markClassName: 'legend-tool legend-tool-' + shape,
        };
      }),
  };
}

export type ScatterGeometry = {
  width: number;
  height: number;
  margin: { left: number; right: number; top: number; bottom: number };
  /** The plot area inside the margins. */
  plot: { x: number; y: number; width: number; height: number };
  /** The x domain's upper bound; `X(price) = plot.x + (price / axisMax) * plot.width`. */
  axisMax: number;
  xTicks: Array<{ value: number; x: number; label: string }>;
  yTicks: Array<{ value: number; y: number; label: string }>;
} & ScatterLayer;

/**
 * The whole plot's geometry. report.py:585.
 *
 * The x domain ends just past the priciest point rather than at the next round tick: `niceTicks` always
 * overshoots to a whole step, which donated up to a quarter of the plot width to empty space and
 * squeezed the cluster into the left edge. Ticks are whatever round values still fit.
 *
 */
export function scatterSvg(
  points: readonly ScatterPoint[],
  tools: readonly Tool[],
  layout: ScatterLayout = 'desktop',
): ScatterGeometry {
  const plotted = points.filter(isPlotted);
  const canvas = SCATTER_CANVASES[layout];
  const { width, height, margin } = canvas;
  const pw = width - margin.left - margin.right;
  const ph = height - margin.top - margin.bottom;

  const maxPrice = plotted.reduce((max, point) => (point.price > max ? point.price : max), 0);
  const axisMax = maxPrice * 1.04 || 1;
  const X = (price: number): number => margin.left + (price / axisMax) * pw;
  const Y = (score: number): number => margin.top + (1 - score) * ph;

  const labels = new Map(tools.map((tool) => [tool.id, tool.label]));
  const toolLabel = (toolId: string): string => labels.get(toolId) ?? toolId;

  const dots: ScatterDot[] = plotted.map((point) => ({
    label: point.label,
    toolId: point.toolId,
    price: point.price,
    completion: point.completion,
    n: point.n,
    family: point.family,
    cx: X(point.price),
    cy: Y(point.completion),
  }));

  return {
    width,
    height,
    margin,
    plot: { x: margin.left, y: margin.top, width: pw, height: ph },
    axisMax,
    xTicks: niceTicks(maxPrice)
      .filter((value) => value <= axisMax)
      .map((value) => ({ value, x: X(value), label: fmtPrice(value) })),
    yTicks: [0, 0.25, 0.5, 0.75, 1.0].map((value) => ({
      value,
      y: Y(value),
      label: String(Math.round(value * 100)),
    })),
    ...scatterLayer(dots, famVariants(plotted), toolLabel, canvas),
  };
}
