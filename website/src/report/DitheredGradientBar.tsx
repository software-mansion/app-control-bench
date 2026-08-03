import { useEffect, useRef } from 'preact/hooks';
import tgpu, { d, std } from 'typegpu';

import { getReportGpuRoot } from './gpuRoot';

type Segment = {
  width: number;
  color: '--ink' | '--fill' | '--line2' | '--ok' | '--warn' | '--bad';
};

type DitheredGradientBarProps = {
  label: string;
  segments: readonly Segment[];
  /**
   * Ramp the dither from sparse to solid across the bar. Right for a single magnitude bar, wrong for
   * a stacked composition: there the value is the segment's length, and a ramp starves whichever
   * verdict sits at the sparse end. Those bars pass `false` and get a flat, evenly dithered tone.
   */
  ramp?: boolean;
};

type DitheredGradientBarHandle = {
  update: (segments: readonly Segment[]) => void;
  dispose: () => void;
};

const GradientState = d.struct({
  hover: d.f32,
  fill: d.f32,
  ditherCellSize: d.f32,
  time: d.f32,
  // Total drawn extent. The density ramp is normalized against it, so a lone bar ramps across its
  // own length and a stacked composition ramps once across the whole track rather than per segment.
  span: d.f32,
  rampStrength: d.f32,
  gridWidth: d.u32,
  gridHeight: d.u32,
  algorithm: d.u32,
});

const GradientSegment = d.struct({
  range: d.vec2f,
  baseColor: d.vec4f,
});

const corners = tgpu.vertexLayout(d.arrayOf(d.vec2f));
const segmentLayout = tgpu.vertexLayout(d.arrayOf(GradientSegment), 'instance');
const BlueNoiseRanks = d.arrayOf(d.u32, 256);
const MAX_GRID_WIDTH = 512;
const MAX_GRID_HEIGHT = 64;
const MAX_GRID_CELLS = MAX_GRID_WIDTH * MAX_GRID_HEIGHT;
const MIN_GRADIENT_DENSITY = 0.07;

// Coverage for a flat (unramped) bar. High enough that a thin segment still reads as its own colour,
// low enough that the dither grain is still the texture rather than a flat swatch.
const FLAT_DENSITY = 0.82;

// Every bar runs ordered Bayer; the error-diffusion kernels stay wired up but idle. They cold-start
// from zero accumulated error, which is what left the bar's first cells blank.
const DITHER_ALGORITHM: number = 0;

// At the floor density a Bayer cell only clears its threshold once in ~14, so the bar's head read as
// blank. Relieving the threshold over the first cells keeps the gradient intact and only makes the
// head easier to cross.
const LEAD_RELIEF = 0.5;
const LEAD_RELIEF_SPAN = 6;

// Holding the cursor floods the bar: cells switch to solid in blue-noise rank order, so the fill
// reads as noise closing in rather than a wipe. Releasing snaps back to the gradient much faster.
const FILL_SECONDS = 1.0;
const UNFILL_SECONDS = 0.3;

// A 16x16 void-and-cluster rank tile. Bayer carries the ordered gradient; this tile corrects
// its periodic residual without turning the result back into unstructured white noise.
const BLUE_NOISE_RANKS = [
  45, 109, 241, 160, 95, 41, 80, 224, 31, 239, 155, 73, 247, 147, 90, 234,
  133, 152, 84, 214, 57, 253, 13, 65, 168, 44, 212, 52, 179, 107, 166, 4,
  210, 26, 181, 19, 129, 202, 162, 121, 185, 137, 101, 7, 201, 36, 71, 194,
  94, 250, 118, 72, 226, 143, 103, 235, 82, 16, 242, 88, 221, 127, 238, 50,
  144, 63, 167, 37, 189, 2, 49, 25, 216, 195, 117, 145, 59, 158, 20, 176,
  228, 11, 206, 106, 240, 91, 177, 148, 69, 40, 173, 23, 255, 190, 100, 120,
  29, 85, 149, 43, 124, 60, 199, 249, 131, 225, 154, 81, 111, 0, 75, 215,
  135, 187, 245, 172, 220, 159, 76, 112, 6, 96, 54, 207, 180, 231, 42, 163,
  55, 67, 5, 97, 30, 15, 209, 46, 237, 186, 32, 125, 64, 140, 196, 248,
  104, 205, 128, 232, 142, 116, 174, 87, 139, 164, 217, 244, 17, 93, 34, 115,
  156, 178, 79, 51, 192, 252, 62, 24, 197, 12, 105, 78, 151, 169, 218, 14,
  227, 22, 211, 35, 89, 153, 219, 134, 233, 66, 119, 48, 200, 236, 130, 86,
  47, 146, 243, 165, 108, 1, 183, 102, 39, 157, 251, 182, 3, 38, 70, 191,
  8, 98, 123, 58, 230, 27, 74, 53, 170, 222, 83, 141, 99, 161, 113, 254,
  138, 184, 68, 198, 136, 208, 246, 126, 9, 204, 21, 61, 229, 213, 56, 171,
  77, 223, 33, 10, 175, 114, 150, 193, 92, 110, 132, 188, 28, 122, 18, 203,
];

const bayerDigit = tgpu.fn([d.f32, d.f32], d.f32)((x, y) => {
  'use gpu';
  return x * 2 + y * 3 - x * y * 4;
});

const bayer8 = tgpu.fn([d.vec2f], d.f32)((cell) => {
  'use gpu';
  const x0 = std.mod(cell.x, 2);
  const y0 = std.mod(cell.y, 2);
  const x1 = std.mod(std.floor(cell.x / 2), 2);
  const y1 = std.mod(std.floor(cell.y / 2), 2);
  const x2 = std.mod(std.floor(cell.x / 4), 2);
  const y2 = std.mod(std.floor(cell.y / 4), 2);
  const rank = bayerDigit(x0, y0) * 16 + bayerDigit(x1, y1) * 4 + bayerDigit(x2, y2);
  return (rank + 0.5) / 64;
});

function colorFromCss(canvas: HTMLCanvasElement, variable: Segment['color']): [number, number, number, number] {
  const probe = document.createElement('span');
  probe.style.color = `var(${variable})`;
  canvas.parentElement?.append(probe);
  const channels = getComputedStyle(probe).color.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 1];
  probe.remove();
  return [channels[0] / 255, channels[1] / 255, channels[2] / 255, channels[3] ?? 1];
}

function instances(canvas: HTMLCanvasElement, segments: readonly Segment[]) {
  let start = 0;
  return segments.map((segment) => {
    const width = Math.max(0, Math.min(1, segment.width));
    const color = colorFromCss(canvas, segment.color);
    const value = { range: d.vec2f(start, width), baseColor: d.vec4f(...color) };
    start += width;
    return value;
  });
}

function totalWidth(segments: readonly Segment[]): number {
  return Math.max(0, Math.min(1, segments.reduce((sum, segment) => sum + Math.max(0, segment.width), 0)));
}

async function installDitheredGradientBar(
  canvas: HTMLCanvasElement,
  initialSegments: readonly Segment[],
  ramp: boolean,
): Promise<DitheredGradientBarHandle> {
  const root = await getReportGpuRoot();
  const context = root.configureContext({ canvas, alphaMode: 'premultiplied' });
  const quadBuffer = root
    .createBuffer(corners.schemaForCount(6), [
      [0, 0],
      [1, 0],
      [0, 1],
      [0, 1],
      [1, 0],
      [1, 1],
    ])
    .$usage('vertex');
  const instanceBuffer = root
    .createBuffer(segmentLayout.schemaForCount(initialSegments.length), instances(canvas, initialSegments))
    .$usage('vertex');
  const blueNoise = root.createReadonly(BlueNoiseRanks, BLUE_NOISE_RANKS);
  const diffusionErrors = root.createMutable(d.arrayOf(d.f32, MAX_GRID_CELLS));
  const diffusionMask = root.createMutable(d.arrayOf(d.f32, MAX_GRID_CELLS));
  const state = root.createUniform(GradientState, {
    hover: 0,
    fill: 0,
    ditherCellSize: 1,
    time: 0,
    span: totalWidth(initialSegments),
    rampStrength: ramp ? 1 : 0,
    gridWidth: 1,
    gridHeight: 1,
    algorithm: DITHER_ALGORITHM,
  });

  const blueNoiseAt = tgpu.fn([d.vec2f, d.f32], d.f32)((cell, frame) => {
    'use gpu';
    const x = d.u32(std.mod(cell.x + frame * 5, 16));
    const y = d.u32(std.mod(cell.y + frame * 3, 16));
    return (d.f32(blueNoise.$[y * 16 + x]) + 0.5) / 256;
  });

  // Fill order. The raw 16x16 tile would flood every tile in lockstep, so each tile is nudged by a
  // hash of its own coordinates: the ranks stay locally even, the tiling stops being readable.
  const fillRank = tgpu.fn([d.vec2f], d.f32)((cell) => {
    'use gpu';
    const tile = std.floor(cell.div(16));
    const jitter = std.fract(std.sin(tile.x * 127.1 + tile.y * 311.7) * 43758.5453);
    // The floor stays above zero so a resting bar (fill 0) has no cell already past its rank.
    return std.clamp(blueNoiseAt(cell, 0) + (jitter - 0.5) * 0.12, 0.004, 1);
  });

  const addDiffusionError = tgpu.fn([d.i32, d.i32, d.f32])((x, y, amount) => {
    'use gpu';
    if (
      x >= 0 &&
      x < d.i32(state.$.gridWidth) &&
      y >= 0 &&
      y < d.i32(state.$.gridHeight)
    ) {
      const offset = d.u32(y) * MAX_GRID_WIDTH + d.u32(x);
      diffusionErrors.$[offset] = diffusionErrors.$[offset] + amount;
    }
  });

  const diffuseFloydSteinberg = tgpu.fn([d.i32, d.i32, d.i32, d.f32])((x, y, direction, error) => {
    'use gpu';
    addDiffusionError(x + direction, y, (error * 7) / 16);
    addDiffusionError(x - direction, y + 1, (error * 3) / 16);
    addDiffusionError(x, y + 1, (error * 5) / 16);
    addDiffusionError(x + direction, y + 1, error / 16);
  });

  const diffuseAtkinson = tgpu.fn([d.i32, d.i32, d.i32, d.f32])((x, y, direction, error) => {
    'use gpu';
    const share = error / 8;
    addDiffusionError(x + direction, y, share);
    addDiffusionError(x + direction * 2, y, share);
    addDiffusionError(x - direction, y + 1, share);
    addDiffusionError(x, y + 1, share);
    addDiffusionError(x + direction, y + 1, share);
    addDiffusionError(x, y + 2, share);
  });

  const diffuseStucki = tgpu.fn([d.i32, d.i32, d.i32, d.f32])((x, y, direction, error) => {
    'use gpu';
    addDiffusionError(x + direction, y, (error * 8) / 42);
    addDiffusionError(x + direction * 2, y, (error * 4) / 42);
    addDiffusionError(x - direction * 2, y + 1, (error * 2) / 42);
    addDiffusionError(x - direction, y + 1, (error * 4) / 42);
    addDiffusionError(x, y + 1, (error * 8) / 42);
    addDiffusionError(x + direction, y + 1, (error * 4) / 42);
    addDiffusionError(x + direction * 2, y + 1, (error * 2) / 42);
    addDiffusionError(x - direction * 2, y + 2, error / 42);
    addDiffusionError(x - direction, y + 2, (error * 2) / 42);
    addDiffusionError(x, y + 2, (error * 4) / 42);
    addDiffusionError(x + direction, y + 2, (error * 2) / 42);
    addDiffusionError(x + direction * 2, y + 2, error / 42);
  });

  const diffusionCompute = tgpu.computeFn({ workgroupSize: [1] })(() => {
    'use gpu';
    const gridWidth = d.i32(state.$.gridWidth);
    const gridHeight = d.i32(state.$.gridHeight);

    for (const y of std.range(MAX_GRID_HEIGHT)) {
      if (y >= gridHeight) break;
      for (const x of std.range(MAX_GRID_WIDTH)) {
        if (x >= gridWidth) break;
        const offset = d.u32(y) * MAX_GRID_WIDTH + d.u32(x);
        diffusionErrors.$[offset] = 0;
        diffusionMask.$[offset] = 0;
      }
    }

    for (const y of std.range(MAX_GRID_HEIGHT)) {
      if (y >= gridHeight) break;
      const reverse = std.mod(y, 2) === 1;
      const direction = std.select(d.i32(1), d.i32(-1), reverse);
      for (const scanX of std.range(MAX_GRID_WIDTH)) {
        if (scanX >= gridWidth) break;
        const x = std.select(scanX, gridWidth - 1 - scanX, reverse);
        const offset = d.u32(y) * MAX_GRID_WIDTH + d.u32(x);
        const position = d.vec2f(d.f32(x), d.f32(y));
        const blueTime = state.$.time * 0.48;
        const blueFrame = std.floor(blueTime);
        const blueBlend = std.smoothstep(0, 1, std.fract(blueTime));
        const staticBlue = blueNoiseAt(position, 0);
        const animatedBlue = std.mix(
          blueNoiseAt(position, blueFrame),
          blueNoiseAt(position, blueFrame + 1),
          blueBlend,
        );
        const denominator = std.max(d.f32(gridWidth - 1), 1);
        const density =
          MIN_GRADIENT_DENSITY +
          std.smoothstep(0.02, 0.98, d.f32(x) / denominator) * (1 - MIN_GRADIENT_DENSITY);
        const thresholdOffset = (animatedBlue - staticBlue) * state.$.hover * 0.18;
        const value = density + diffusionErrors.$[offset] - thresholdOffset;
        const visible = std.step(0.5, value);
        const error = value - visible;
        diffusionMask.$[offset] = visible;

        if (state.$.algorithm === 1) {
          diffuseFloydSteinberg(x, y, direction, error);
        } else if (state.$.algorithm === 2) {
          diffuseAtkinson(x, y, direction, error);
        } else {
          diffuseStucki(x, y, direction, error);
        }
      }
    }
  });

  const vertex = tgpu.vertexFn({
    in: { corner: d.vec2f, range: d.vec2f, baseColor: d.vec4f },
    out: { position: d.builtin.position, ramp: d.f32, baseColor: d.vec4f },
  })((input) => {
    'use gpu';
    const x = input.range.x + input.corner.x * input.range.y;
    return {
      position: d.vec4f(x * 2 - 1, 1 - input.corner.y * 2, 0, 1),
      ramp: x / std.max(state.$.span, 0.0001),
      baseColor: input.baseColor,
    };
  });

  const fragment = tgpu.fragmentFn({
    in: { position: d.builtin.position, ramp: d.f32, baseColor: d.vec4f },
    out: d.vec4f,
  })((input) => {
    'use gpu';
    const cell = std.floor(input.position.xy.div(state.$.ditherCellSize));
    const blueTime = state.$.time * 0.48;
    const blueFrame = std.floor(blueTime);
    const blueBlend = std.smoothstep(0, 1, std.fract(blueTime));
    const staticBlue = blueNoiseAt(cell, 0);
    const animatedBlue = std.mix(
      blueNoiseAt(cell, blueFrame),
      blueNoiseAt(cell, blueFrame + 1),
      blueBlend,
    );
    const blue = std.mix(staticBlue, animatedBlue, state.$.hover);
    // Lead relief only exists to rescue the ramp's starved head; a flat bar has no such head.
    const relief =
      LEAD_RELIEF * (1 - std.smoothstep(0, LEAD_RELIEF_SPAN, cell.x)) * state.$.rampStrength;
    const threshold = std.clamp(bayer8(cell) + (blue - 0.5) * 0.28 - relief, 0, 1);
    const ramped =
      MIN_GRADIENT_DENSITY +
      std.smoothstep(0.02, 0.98, input.ramp) * (1 - MIN_GRADIENT_DENSITY);
    const density = std.mix(FLAT_DENSITY, ramped, state.$.rampStrength);
    let visible = std.step(threshold, density);
    if (state.$.algorithm > 0) {
      const offset = d.u32(cell.y) * MAX_GRID_WIDTH + d.u32(cell.x);
      visible = diffusionMask.$[offset];
    }
    visible = std.max(visible, std.step(fillRank(cell), state.$.fill));
    const alpha = visible * input.baseColor.a;
    return d.vec4f(input.baseColor.rgb.mul(alpha), alpha);
  });

  const computePipeline = root.createComputePipeline({ compute: diffusionCompute });
  const pipeline = root
    .createRenderPipeline({
      attribs: { corner: corners.attrib, ...segmentLayout.attrib },
      vertex,
      fragment,
    })
    .with(corners, quadBuffer)
    .with(segmentLayout, instanceBuffer);

  let span = totalWidth(initialSegments);
  let hovered = false;
  let hoverProgress = 0;
  let fillProgress = 0;
  let frame: number | undefined;
  let lastTimestamp = performance.now();
  let pixelRatio = 1;
  let cssWidth = 1;
  let cssHeight = 1;

  const requestDraw = () => {
    frame ??= requestAnimationFrame(draw);
  };

  const resize = () => {
    const bounds = canvas.getBoundingClientRect();
    cssWidth = Math.max(1, bounds.width);
    cssHeight = Math.max(1, bounds.height);
    pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const nextWidth = Math.max(1, Math.round(bounds.width * pixelRatio));
    const nextHeight = Math.max(1, Math.round(bounds.height * pixelRatio));
    if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
      canvas.width = nextWidth;
      canvas.height = nextHeight;
    }
    requestDraw();
  };

  const draw = (timestamp: number) => {
    frame = undefined;
    const deltaSeconds = Math.min(0.05, (timestamp - lastTimestamp) / 1000);
    lastTimestamp = timestamp;
    const blend = 1 - Math.exp(-deltaSeconds * 10);
    hoverProgress += ((hovered ? 1 : 0) - hoverProgress) * blend;
    fillProgress = Math.max(
      0,
      Math.min(1, fillProgress + deltaSeconds / (hovered ? FILL_SECONDS : -UNFILL_SECONDS)),
    );
    state.write({
      hover: hoverProgress,
      fill: fillProgress,
      ditherCellSize: pixelRatio,
      time: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : timestamp / 1000,
      span,
      rampStrength: ramp ? 1 : 0,
      gridWidth: Math.min(MAX_GRID_WIDTH, Math.max(1, Math.ceil(cssWidth * span))),
      gridHeight: Math.min(MAX_GRID_HEIGHT, Math.max(1, Math.round(cssHeight))),
      algorithm: DITHER_ALGORITHM,
    });
    if (DITHER_ALGORITHM > 0) computePipeline.dispatchWorkgroups(1);
    pipeline
      .withColorAttachment({ view: context, clearValue: [0, 0, 0, 0], loadOp: 'clear', storeOp: 'store' })
      .draw(6, initialSegments.length);
    if (hovered || hoverProgress > 0.001 || fillProgress > 0) requestDraw();
  };

  const onEnter = () => {
    hovered = true;
    requestDraw();
  };
  const onLeave = () => {
    hovered = false;
    requestDraw();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  canvas.addEventListener('pointerenter', onEnter);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('mouseenter', onEnter);
  canvas.addEventListener('mouseleave', onLeave);
  resize();

  return {
    update(segments) {
      span = totalWidth(segments);
      instanceBuffer.write(instances(canvas, segments));
      requestDraw();
    },
    dispose() {
      if (frame !== undefined) cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener('pointerenter', onEnter);
      canvas.removeEventListener('pointerleave', onLeave);
      canvas.removeEventListener('mouseenter', onEnter);
      canvas.removeEventListener('mouseleave', onLeave);
      quadBuffer.destroy();
      instanceBuffer.destroy();
      blueNoise.buffer.destroy();
      diffusionErrors.buffer.destroy();
      diffusionMask.buffer.destroy();
      state.buffer.destroy();
      context.unconfigure();
    },
  };
}

export function DitheredGradientBar({ label, segments, ramp = true }: DitheredGradientBarProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const handleRef = useRef<DitheredGradientBarHandle | null>(null);
  const segmentsRef = useRef(segments);
  segmentsRef.current = segments;
  // Read once at install: the ramp is a fixed property of the chart, not something a row toggles.
  const rampRef = useRef(ramp);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !('gpu' in navigator)) return;
    let mounted = true;
    void installDitheredGradientBar(canvas, segmentsRef.current, rampRef.current)
      .then((handle) => {
        if (!mounted) {
          handle.dispose();
          return;
        }
        handleRef.current = handle;
        handle.update(segmentsRef.current);
        canvas.dataset.gpuReady = 'true';
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
      handleRef.current?.dispose();
      handleRef.current = null;
    };
  }, []);

  useEffect(() => {
    handleRef.current?.update(segments);
  }, [segments]);

  return (
    <span class="dithered-gradient-host">
      <canvas ref={canvasRef} class="dithered-gradient-bar" aria-label={label} />
      <span class={ramp ? 'dithered-gradient-fallback' : 'dithered-gradient-fallback flat'} aria-hidden="true">
        {segments.map((segment, index) => (
          <i
            key={index}
            style={{
              width: `${(Math.max(0, Math.min(1, segment.width)) * 100).toFixed(1)}%`,
              color: `var(${segment.color})`,
            }}
          />
        ))}
      </span>
    </span>
  );
}
