import { useEffect, useRef } from 'preact/hooks';
import tgpu, { d, std } from 'typegpu';

import { bayer8 } from './bayer8';
import { getReportGpuRoot } from './gpuRoot';

type Segment = {
  width: number;
  color: '--ink' | '--fill' | '--line2' | '--ok' | '--warn' | '--bad';
};

type DitheredGradientBarProps = {
  label: string;
  segments: readonly [Segment];
};

type DitheredGradientBarHandle = {
  update: (segments: readonly [Segment]) => void;
  dispose: () => void;
};

const GradientState = d.struct({
  hover: d.f32,
  fill: d.f32,
  ditherCellSize: d.f32,
  time: d.f32,
});

const GradientSegment = d.struct({
  width: d.f32,
  baseColor: d.vec4f,
});

const corners = tgpu.vertexLayout(d.arrayOf(d.vec2f));
const segmentLayout = tgpu.vertexLayout(d.arrayOf(GradientSegment), 'instance');
const BlueNoiseRanks = d.arrayOf(d.u32, 256);
const MIN_GRADIENT_DENSITY = 0.07;

// At the floor density a Bayer cell only clears its threshold once in ~14, so the bar's head read as
// blank. Relieving the threshold over the first cells keeps the gradient intact and only makes the
// head easier to cross.
const LEAD_RELIEF = 0.5;
const LEAD_RELIEF_SPAN = 6;

// Holding the cursor floods the bar: cells switch to solid in blue-noise rank order, so the fill
// reads as noise closing in rather than a wipe. Releasing snaps back to the gradient much faster.
const FILL_SECONDS = 0.3;
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

function colorFromCss(canvas: HTMLCanvasElement, variable: Segment['color']): [number, number, number, number] {
  const probe = document.createElement('span');
  probe.style.color = `var(${variable})`;
  canvas.parentElement?.append(probe);
  const channels = getComputedStyle(probe).color.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 1];
  probe.remove();
  return [channels[0] / 255, channels[1] / 255, channels[2] / 255, channels[3] ?? 1];
}

function segmentWidth(segments: readonly [Segment]): number {
  return Math.max(0, Math.min(1, segments[0].width));
}

function instances(canvas: HTMLCanvasElement, segments: readonly [Segment]) {
  const segment = segments[0];
  return [
    {
      width: segmentWidth(segments),
      baseColor: d.vec4f(...colorFromCss(canvas, segment.color)),
    },
  ];
}

async function installDitheredGradientBar(
  canvas: HTMLCanvasElement,
  initialSegments: readonly [Segment],
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
    .createBuffer(segmentLayout.schemaForCount(1), instances(canvas, initialSegments))
    .$usage('vertex');
  const blueNoise = root.createReadonly(BlueNoiseRanks, BLUE_NOISE_RANKS);
  const state = root.createUniform(GradientState, {
    hover: 0,
    fill: 0,
    ditherCellSize: 1,
    time: 0,
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

  const vertex = tgpu.vertexFn({
    in: { corner: d.vec2f, width: d.f32, baseColor: d.vec4f },
    out: { position: d.builtin.position, ramp: d.f32, baseColor: d.vec4f },
  })((input) => {
    'use gpu';
    const x = input.corner.x * input.width;
    return {
      position: d.vec4f(x * 2 - 1, 1 - input.corner.y * 2, 0, 1),
      ramp: input.corner.x,
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
    const relief = LEAD_RELIEF * (1 - std.smoothstep(0, LEAD_RELIEF_SPAN, cell.x));
    const threshold = std.clamp(bayer8(cell) + (blue - 0.5) * 0.28 - relief, 0, 1);
    const density =
      MIN_GRADIENT_DENSITY +
      std.smoothstep(0.02, 0.98, input.ramp) * (1 - MIN_GRADIENT_DENSITY);
    let visible = std.step(threshold, density);
    visible = std.max(visible, std.step(fillRank(cell), state.$.fill));
    const alpha = visible * input.baseColor.a;
    return d.vec4f(input.baseColor.rgb.mul(alpha), alpha);
  });

  const pipeline = root
    .createRenderPipeline({
      attribs: { corner: corners.attrib, ...segmentLayout.attrib },
      vertex,
      fragment,
    })
    .with(corners, quadBuffer)
    .with(segmentLayout, instanceBuffer);

  let hovered = false;
  let hoverProgress = 0;
  let fillProgress = 0;
  let frame: number | undefined;
  let lastTimestamp = performance.now();
  let pixelRatio = 1;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const requestDraw = () => {
    frame ??= requestAnimationFrame(draw);
  };

  const resize = () => {
    const bounds = canvas.getBoundingClientRect();
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
    const hoverTarget = hovered ? 1 : 0;
    const fillTarget = hovered ? 1 : 0;
    if (reducedMotion.matches) {
      hoverProgress = hoverTarget;
      fillProgress = fillTarget;
    } else {
      const blend = 1 - Math.exp(-deltaSeconds * 10);
      hoverProgress += (hoverTarget - hoverProgress) * blend;
      if (Math.abs(hoverProgress - hoverTarget) <= 0.001) hoverProgress = hoverTarget;
      fillProgress = Math.max(
        0,
        Math.min(1, fillProgress + deltaSeconds / (hovered ? FILL_SECONDS : -UNFILL_SECONDS)),
      );
    }
    state.write({
      hover: hoverProgress,
      fill: fillProgress,
      ditherCellSize: pixelRatio,
      time: reducedMotion.matches ? 0 : timestamp / 1000,
    });
    pipeline
      .withColorAttachment({ view: context, clearValue: [0, 0, 0, 0], loadOp: 'clear', storeOp: 'store' })
      .draw(6, 1);
    const hoverSettled = hoverProgress === hoverTarget;
    const fillSettled = fillProgress === fillTarget;
    if (!reducedMotion.matches && (hovered || !hoverSettled || !fillSettled)) requestDraw();
  };

  const onEnter = () => {
    hovered = true;
    requestDraw();
  };
  const onLeave = () => {
    hovered = false;
    requestDraw();
  };
  const onMotionChange = () => requestDraw();
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  reducedMotion.addEventListener('change', onMotionChange);
  canvas.addEventListener('pointerenter', onEnter);
  canvas.addEventListener('pointerleave', onLeave);
  resize();

  return {
    update(segments) {
      instanceBuffer.write(instances(canvas, segments));
      requestDraw();
    },
    dispose() {
      if (frame !== undefined) cancelAnimationFrame(frame);
      observer.disconnect();
      reducedMotion.removeEventListener('change', onMotionChange);
      canvas.removeEventListener('pointerenter', onEnter);
      canvas.removeEventListener('pointerleave', onLeave);
      quadBuffer.destroy();
      instanceBuffer.destroy();
      blueNoise.buffer.destroy();
      state.buffer.destroy();
      context.unconfigure();
    },
  };
}

export function DitheredGradientBar({ label, segments }: DitheredGradientBarProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const handleRef = useRef<DitheredGradientBarHandle | null>(null);
  const segmentsRef = useRef(segments);
  segmentsRef.current = segments;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !('gpu' in navigator)) return;
    let mounted = true;
    void installDitheredGradientBar(canvas, segmentsRef.current)
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

  const segment = segments[0];
  return (
    <span class="dithered-gradient-host">
      <canvas ref={canvasRef} class="dithered-gradient-bar" aria-label={label} />
      <span class="dithered-gradient-fallback" aria-hidden="true">
        <i
          style={{
            width: `${(segmentWidth(segments) * 100).toFixed(1)}%`,
            color: `var(${segment.color})`,
          }}
        />
      </span>
    </span>
  );
}
