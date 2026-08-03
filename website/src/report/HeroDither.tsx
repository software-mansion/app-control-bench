import { useEffect, useRef } from 'preact/hooks';
import { perlin2d } from '@typegpu/noise';
import tgpu, { common, d, std } from 'typegpu';

import { ditheredChromaticDisturbance } from './ditheredChromaticDisturbance';
import { getReportGpuRoot } from './gpuRoot';

type HeroDitherHandle = { dispose: () => void };

type TrailPoint = { x: number; y: number; age: number; speed: number };

const TRAIL_COUNT = 40;

function cssColor(element: HTMLElement): [number, number, number] {
  const channels = getComputedStyle(element).color.match(/[\d.]+/g)?.map(Number);
  if (!channels || channels.length < 3) throw new Error('Unable to resolve the hero paper color');
  return [channels[0] / 255, channels[1] / 255, channels[2] / 255];
}

async function installHeroDither(canvas: HTMLCanvasElement): Promise<HeroDitherHandle> {
  const root = await getReportGpuRoot();
  const presentationFormat = navigator.gpu.getPreferredCanvasFormat();
  const context = root.configureContext({
    canvas,
    format: presentationFormat,
    alphaMode: 'premultiplied',
  });
  const viewport = root.createUniform(d.vec2f, d.vec2f(1));
  const animationTime = root.createUniform(d.f32, 0);
  const pointerData = root.createUniform(d.vec4f, d.vec4f(0.5, 0.5, 0, 0));
  const paperColor = root.createUniform(d.vec3f, cssColor(canvas));
  const trailUpload = new Float32Array(TRAIL_COUNT * 4);
  for (let index = 0; index < TRAIL_COUNT; index += 1) {
    trailUpload.set([4, 4, 1, 0], index * 4);
  }
  const trailPoints = root.createUniform(d.arrayOf(d.vec4f, TRAIL_COUNT), trailUpload);
  const perlinCache = perlin2d.staticCache({ root, size: d.vec2u(64, 64) });

  const trailInfluenceAt = (uv: d.v2f, aspect: number) => {
    'use gpu';
    let influence = d.f32(0);
    for (const trailPoint of trailPoints.$) {
      if (trailPoint.z >= 1) break;
      const delta = uv.sub(trailPoint.xy).mul(d.vec2f(aspect, 1));
      const distance = std.length(delta);
      const life = std.clamp(1 - trailPoint.z, 0, 1);
      const fastRadius = std.mix(0.072, 0.022, std.clamp(trailPoint.w * 0.7, 0, 1));
      const pointInfluence = std.exp((-distance * distance) / (fastRadius * fastRadius)) * life;
      influence += pointInfluence * std.mix(0.4, 1.15, std.clamp(trailPoint.w, 0, 1));
    }
    return std.clamp(influence, 0, 1);
  };

  const fragment = tgpu.fragmentFn({
    in: { uv: d.vec2f, pixel: d.builtin.position },
    out: d.vec4f,
  })(({ uv, pixel }) => {
    'use gpu';
    const aspect = viewport.$.x / viewport.$.y;
    return d.vec4f(
      ditheredChromaticDisturbance(
        uv,
        pixel.xy,
        pointerData.$.xy,
        pointerData.$.zw,
        animationTime.$,
        aspect,
        trailInfluenceAt(uv, aspect),
        paperColor.$,
      ),
      1,
    );
  });

  const pipeline = root.pipe(perlinCache.inject()).createRenderPipeline({
    vertex: common.fullScreenTriangle,
    fragment,
    targets: { format: presentationFormat },
  });

  const trail: TrailPoint[] = [];
  let pointerTarget: [number, number] = [0.5, 0.5];
  let pointerCurrent: [number, number] = [0.5, 0.5];
  let pointerVelocity: [number, number] = [0, 0];
  let lastTrailPosition: [number, number] = [0.5, 0.5];
  let pointerSeen = false;
  let lastPointerSample = 0;
  let previousTime = 0;
  let elapsedTime = 0;
  let frame: number | undefined;
  let inView = true;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const requestDraw = () => {
    frame ??= requestAnimationFrame(draw);
  };

  const resizeCanvas = () => {
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.min(
      root.device.limits.maxTextureDimension2D,
      Math.max(1, Math.round(canvas.clientWidth * pixelRatio)),
    );
    const height = Math.min(
      root.device.limits.maxTextureDimension2D,
      Math.max(1, Math.round(canvas.clientHeight * pixelRatio)),
    );
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    viewport.write(d.vec2f(width, height));
    requestDraw();
  };

  const updateTrail = (deltaSeconds: number) => {
    for (const point of trail) point.age += deltaSeconds / 0.18;
    while (trail[0]?.age >= 1) trail.shift();

    const distanceFromLast = Math.hypot(
      pointerCurrent[0] - lastTrailPosition[0],
      pointerCurrent[1] - lastTrailPosition[1],
    );
    const speed = Math.min(1, Math.hypot(pointerVelocity[0], pointerVelocity[1]) * 0.45);
    if (pointerSeen && (elapsedTime - lastPointerSample > 0.045 || distanceFromLast > 0.018)) {
      trail.push({ x: pointerCurrent[0], y: pointerCurrent[1], age: 0, speed });
      if (trail.length > TRAIL_COUNT) trail.shift();
      lastTrailPosition = [pointerCurrent[0], pointerCurrent[1]];
      lastPointerSample = elapsedTime;
    }
    for (let index = 0; index < TRAIL_COUNT; index += 1) {
      const offset = index * 4;
      const point = trail[index];
      trailUpload[offset] = point?.x ?? 4;
      trailUpload[offset + 1] = point?.y ?? 4;
      trailUpload[offset + 2] = point?.age ?? 1;
      trailUpload[offset + 3] = point?.speed ?? 0;
    }
    trailPoints.write(trailUpload);
  };

  const draw = (timestamp: number) => {
    frame = undefined;
    const deltaSeconds = previousTime === 0 ? 0 : Math.min((timestamp - previousTime) / 1000, 0.1);
    previousTime = timestamp;
    if (!reducedMotion.matches) elapsedTime += deltaSeconds;

    const previousPointer: [number, number] = [pointerCurrent[0], pointerCurrent[1]];
    const ease = reducedMotion.matches ? 1 : 1 - Math.exp(-deltaSeconds * 16);
    pointerCurrent[0] += (pointerTarget[0] - pointerCurrent[0]) * ease;
    pointerCurrent[1] += (pointerTarget[1] - pointerCurrent[1]) * ease;
    if (deltaSeconds > 0) {
      const instantVelocity: [number, number] = [
        (pointerCurrent[0] - previousPointer[0]) / deltaSeconds,
        (pointerCurrent[1] - previousPointer[1]) / deltaSeconds,
      ];
      const velocityEase = 1 - Math.exp(-deltaSeconds * 9);
      pointerVelocity[0] += (instantVelocity[0] - pointerVelocity[0]) * velocityEase;
      pointerVelocity[1] += (instantVelocity[1] - pointerVelocity[1]) * velocityEase;
    }
    updateTrail(deltaSeconds);

    pointerData.write(
      d.vec4f(
        pointerCurrent[0],
        pointerCurrent[1],
        pointerVelocity[0] * 0.04,
        pointerVelocity[1] * 0.04,
      ),
    );
    animationTime.write(reducedMotion.matches ? 0 : elapsedTime % 3600);
    pipeline.withColorAttachment({ view: context }).draw(3);

    if (inView && !document.hidden && !reducedMotion.matches) requestDraw();
  };

  const updatePointer = (event: PointerEvent) => {
    if (reducedMotion.matches) return;
    const bounds = canvas.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width;
    const y = (event.clientY - bounds.top) / bounds.height;
    pointerSeen = x >= 0 && x <= 1 && y >= 0 && y <= 1;
    if (!pointerSeen) return;
    pointerTarget = [x, y];
    canvas.parentElement?.querySelector('.benchmark-cursor-hint')?.classList.add('is-hidden');
    requestDraw();
  };
  const onMotionChange = () => {
    previousTime = performance.now();
    if (reducedMotion.matches) {
      pointerSeen = false;
      pointerVelocity = [0, 0];
    }
    requestDraw();
  };
  const onVisibilityChange = () => {
    previousTime = performance.now();
    if (!document.hidden) requestDraw();
  };

  const resizeObserver = new ResizeObserver(resizeCanvas);
  const intersectionObserver = new IntersectionObserver(([entry]) => {
    inView = entry?.isIntersecting ?? true;
    previousTime = performance.now();
    if (inView) requestDraw();
  });
  resizeObserver.observe(canvas);
  intersectionObserver.observe(canvas);
  reducedMotion.addEventListener('change', onMotionChange);
  window.addEventListener('pointermove', updatePointer, { passive: true });
  window.addEventListener('pointerdown', updatePointer, { passive: true });
  document.addEventListener('visibilitychange', onVisibilityChange);
  resizeCanvas();

  return {
    dispose() {
      if (frame !== undefined) cancelAnimationFrame(frame);
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
      reducedMotion.removeEventListener('change', onMotionChange);
      window.removeEventListener('pointermove', updatePointer);
      window.removeEventListener('pointerdown', updatePointer);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      viewport.buffer.destroy();
      animationTime.buffer.destroy();
      pointerData.buffer.destroy();
      paperColor.buffer.destroy();
      trailPoints.buffer.destroy();
      perlinCache.destroy();
      context.unconfigure();
    },
  };
}

export function HeroDither() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !('gpu' in navigator)) return;
    let mounted = true;
    let handle: HeroDitherHandle | undefined;
    void installHeroDither(canvas)
      .then((installed) => {
        if (!mounted) {
          installed.dispose();
          return;
        }
        handle = installed;
        canvas.dataset.gpuReady = 'true';
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
      handle?.dispose();
    };
  }, []);

  return <canvas ref={canvasRef} class="hero-dither" aria-hidden="true" />;
}
