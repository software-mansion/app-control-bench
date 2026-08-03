// Average price per task against completion score, one point per model x tool. Ports report.py's
// price_scatter (631) plus the interaction half of TOGGLE_JS (1688-1781): click-to-filter legend
// filtering, and the hover/focus tooltip with its SVG crosshair.
//
// Geometry comes from web/src/shared/scatter.ts as data; nothing here computes a coordinate.
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';

import type { ReportView, Tool } from '../shared/contract';
import { fmtCoord } from '../shared/format';
import { hasScatterData, scatterLegend, scatterSvg, type ScatterLayout } from '../shared/scatter';
import { SectionHeader } from '../ui/Chrome';

type Selection = { model: string | null; tool: string | null };

const NONE: Selection = { model: null, tool: null };

function layoutFor(width: number): ScatterLayout {
  if (width <= 480) return 'phone';
  if (width <= 700) return 'compact';
  return 'desktop';
}

function useScatterLayout(): ScatterLayout {
  const [layout, setLayout] = useState<ScatterLayout>('desktop');

  useEffect(() => {
    const update = () => setLayout(layoutFor(window.innerWidth));
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  return layout;
}

function active(sel: Selection): boolean {
  return sel.model !== null || sel.tool !== null;
}

function matches(sel: Selection, model: string, tool: string): boolean {
  return (sel.model === null || sel.model === model) && (sel.tool === null || sel.tool === tool);
}

/** `is-muted` on everything the current selection excludes. report.py TOGGLE_JS:1688. */
function muteClass(base: string, sel: Selection, model: string, tool: string): string {
  return active(sel) && !matches(sel, model, tool) ? `${base} is-muted` : base;
}

function pressed(sel: Selection, model: string, tool: string): 'true' | 'false' {
  return active(sel) && matches(sel, model, tool) ? 'true' : 'false';
}

type Hover = { cx: number; cy: number; tip: string; stroke: string } | null;

export function CostEfficiencyPlot({ view, tools }: { view: ReportView; tools: Tool[] }) {
  const [sel, setSel] = useState<Selection>(NONE);
  const [hover, setHover] = useState<Hover>(null);
  const [tipAt, setTipAt] = useState<{ x: number; y: number } | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const layout = useScatterLayout();

  const geometry = useMemo(
    () => scatterSvg(view.scatter.points, tools, layout),
    [view.scatter, tools, layout],
  );
  const legend = useMemo(() => scatterLegend(view.scatter.points, tools), [view.scatter.points, tools]);

  // The tooltip is positioned against the viewport, so it can only be placed once it has a size —
  // i.e. in an effect, never during prerender.
  useEffect(() => {
    const tip = tipRef.current;
    if (!tip || !hover || !tipAt) return;
    const pad = 10;
    const gap = 12;
    const { offsetWidth: w, offsetHeight: h } = tip;
    let left = Math.min(window.innerWidth - w - pad, tipAt.x + gap);
    let top = tipAt.y - h - gap;
    if (top < pad) top = Math.min(window.innerHeight - h - pad, tipAt.y + gap);
    tip.style.left = `${Math.max(pad, left)}px`;
    tip.style.top = `${Math.max(pad, top)}px`;
  }, [hover, tipAt]);

  if (!hasScatterData(view.scatter.points)) return null;

  const { plot, margin, width, height } = geometry;
  const axisRight = width - margin.right;
  const axisBottom = plot.y + plot.height;

  const show = (el: Element, x?: number, y?: number) => {
    const tip = el.getAttribute('data-scatter-tip');
    const cx = el.getAttribute('data-cx');
    const cy = el.getAttribute('data-cy');
    if (!tip) return;
    const box = el.getBoundingClientRect();
    setTipAt({ x: x ?? box.left + box.width / 2, y: y ?? box.top });
    setHover(
      cx !== null && cy !== null
        ? {
            cx: parseFloat(cx),
            cy: parseFloat(cy),
            tip,
            // The crosshair takes the mark's own colour; .scatter-crosshair sets no stroke of its own.
            stroke: getComputedStyle(el).fill,
          }
        : { cx: NaN, cy: NaN, tip, stroke: '' },
    );
  };

  const hide = () => {
    setHover(null);
    setTipAt(null);
  };

  /** Clicking a mark, a connector or a legend entry toggles that filter. TOGGLE_JS:1701. */
  const selectFrom = (target: EventTarget | null) => {
    const el = target instanceof Element ? target : null;
    const pair = el?.closest('[data-pair]');
    if (pair) {
      const model = pair.getAttribute('data-model');
      const tool = pair.getAttribute('data-tool');
      setSel((s) => (s.model === model && s.tool === tool ? NONE : { model, tool }));
      return;
    }
    const modelEl = el?.closest('[data-legend-model]');
    if (modelEl) {
      const next = modelEl.getAttribute('data-legend-model');
      setSel((s) => ({ ...s, model: s.model === next ? null : next }));
      return;
    }
    const toolEl = el?.closest('[data-legend-tool]');
    if (toolEl) {
      const next = toolEl.getAttribute('data-legend-tool');
      setSel((s) => ({ ...s, tool: s.tool === next ? null : next }));
      return;
    }
    setSel(NONE);
  };

  return (
    <section class="efficiency-section" id="efficiency">
      <SectionHeader
        title="Cost efficiency"
        explain="Average price per task vs completion score. Colour identifies the model, shape the tool; higher and cheaper is better."
      />
      <div
        id="stat-scatter"
        onClick={(e) => selectFrom(e.target)}
        onKeyDown={(e) => {
          const el = e.target instanceof Element ? e.target : null;
          if (
            (e.key === 'Enter' || e.key === ' ') &&
            el?.closest('[data-pair],[data-legend-model],[data-legend-tool]')
          ) {
            e.preventDefault();
            selectFrom(e.target);
          }
        }}
        onPointerOver={(e) => {
          const el = e.target instanceof Element ? e.target.closest('[data-scatter-tip]') : null;
          if (el) show(el, e.clientX, e.clientY);
        }}
        onPointerMove={(e) => {
          const el = e.target instanceof Element ? e.target.closest('[data-scatter-tip]') : null;
          if (el) show(el, e.clientX, e.clientY);
        }}
        onPointerOut={hide}
        onFocusIn={(e) => {
          const el = e.target instanceof Element ? e.target.closest('[data-scatter-tip]') : null;
          if (el) show(el);
        }}
        onFocusOut={hide}
      >
        <div class="scatterlegend" aria-label="Scatterplot legend">
          <div class="scatterlegend-group">
            <span class="scatterlegend-title">model</span>
            <div class="scatterlegend-items">
              {legend.models.map((entry) => (
                <button
                  key={entry.base}
                  type="button"
                  class="scatterlegend-item"
                  data-legend-model={entry.base}
                  aria-pressed={sel.model === entry.base ? 'true' : 'false'}
                >
                  <i class={entry.swatchClassName} />
                  {entry.base}
                </button>
              ))}
            </div>
          </div>
          <div class="scatterlegend-group">
            <span class="scatterlegend-title">tool</span>
            <div class="scatterlegend-items">
              {legend.tools.map((entry) => (
                <button
                  key={entry.toolId}
                  type="button"
                  class="scatterlegend-item"
                  data-legend-tool={entry.toolId}
                  aria-pressed={sel.tool === entry.toolId ? 'true' : 'false'}
                >
                  <i class={entry.markClassName} aria-hidden="true" />
                  {entry.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <section class="panel">
          <div class="scatterwrap">
            <svg
              viewBox={`0 0 ${width} ${height}`}
              class={`scatter scatter--${layout}`}
              preserveAspectRatio="xMidYMid meet"
              role="group"
              aria-label="price per task vs completion score by model and tool"
            >
              {geometry.yTicks.map((tick) => (
                <g key={`y${tick.value}`}>
                  <line class="sg" x1={margin.left} y1={fmtCoord(tick.y)} x2={axisRight} y2={fmtCoord(tick.y)} />
                  <text class="stk" x={margin.left - 9} y={fmtCoord(tick.y + 3.6)} text-anchor="end">
                    {tick.label}
                  </text>
                </g>
              ))}
              {geometry.xTicks.map((tick) => (
                <g key={`x${tick.value}`}>
                  <line class="sg" x1={fmtCoord(tick.x)} y1={margin.top} x2={fmtCoord(tick.x)} y2={axisBottom} />
                  <text class="stk" x={fmtCoord(tick.x)} y={fmtCoord(axisBottom + 18)} text-anchor="middle">
                    {tick.label}
                  </text>
                </g>
              ))}
              <line class="sax-l" x1={margin.left} y1={margin.top} x2={margin.left} y2={axisBottom} />
              <line class="sax-l" x1={margin.left} y1={axisBottom} x2={axisRight} y2={axisBottom} />
              <text class="sax" x={Math.round(margin.left + plot.width / 2)} y={height - 9} text-anchor="middle">
                avg price per task
              </text>
              <text
                class="sax"
                transform={`translate(15,${Math.round(margin.top + plot.height / 2)}) rotate(-90)`}
                text-anchor="middle"
              >
                completion score (%)
              </text>

              {geometry.connectors.map((c) => (
                <>
                  <polyline
                    key={`${c.pair}-line`}
                    class={muteClass(c.className, sel, c.model, c.tool)}
                    data-pair={c.pair}
                    data-model={c.model}
                    data-tool={c.tool}
                    aria-hidden="true"
                    points={c.pointsAttr}
                  />
                  <polyline
                    key={`${c.pair}-hit`}
                    class={c.hitClassName}
                    data-pair={c.pair}
                    data-model={c.model}
                    data-tool={c.tool}
                    role="button"
                    tabIndex={0}
                    aria-pressed={pressed(sel, c.model, c.tool)}
                    aria-label={c.ariaLabel}
                    points={c.pointsAttr}
                  />
                </>
              ))}
              {geometry.leaders.map((l, i) => (
                <polyline
                  key={`lead${i}`}
                  class={muteClass(l.className, sel, l.model, l.tool)}
                  data-pair={l.pair}
                  data-model={l.model}
                  data-tool={l.tool}
                  aria-hidden="true"
                  points={l.pointsAttr}
                />
              ))}
              {geometry.markers.map((m, i) => {
                const shared = {
                  class: muteClass(m.className, sel, m.model, m.tool),
                  'data-pair': m.pair,
                  'data-model': m.model,
                  'data-tool': m.tool,
                  'data-scatter-tip': m.tip,
                  'data-cx': fmtCoord(m.cx),
                  'data-cy': fmtCoord(m.cy),
                  role: 'button' as const,
                  tabIndex: 0,
                  'aria-pressed': pressed(sel, m.model, m.tool),
                  'aria-label': m.ariaLabel,
                };
                const g = m.geometry;
                if (g.shape === 'square') {
                  return <rect key={`m${i}`} {...shared} x={g.x} y={g.y} width={g.width} height={g.height} rx={g.rx} />;
                }
                if (g.shape === 'diamond') return <path key={`m${i}`} {...shared} d={g.d} />;
                return <circle key={`m${i}`} {...shared} cx={fmtCoord(g.cx)} cy={fmtCoord(g.cy)} r={g.r} />;
              })}
              {geometry.labels.map((l, i) => (
                <text
                  key={`t${i}`}
                  class={muteClass(l.className, sel, l.model, l.tool)}
                  data-pair={l.pair}
                  data-model={l.model}
                  data-tool={l.tool}
                  x={fmtCoord(l.x)}
                  y={fmtCoord(l.textY)}
                  text-anchor="middle"
                  aria-hidden="true"
                >
                  {l.text}
                </text>
              ))}
              {hover && Number.isFinite(hover.cx) && (
                <>
                  <line
                    class="scatter-crosshair"
                    aria-hidden="true"
                    x1={hover.cx}
                    x2={hover.cx}
                    y1={hover.cy}
                    y2={axisBottom}
                    style={{ stroke: hover.stroke }}
                  />
                  <line
                    class="scatter-crosshair"
                    aria-hidden="true"
                    x1={margin.left}
                    x2={hover.cx}
                    y1={hover.cy}
                    y2={hover.cy}
                    style={{ stroke: hover.stroke }}
                  />
                </>
              )}
            </svg>
          </div>
        </section>
      </div>
      <div class="scattertip" role="tooltip" ref={tipRef} hidden={!hover}>
        {hover?.tip}
      </div>
    </section>
  );
}
