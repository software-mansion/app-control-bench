import { perlin2d } from '@typegpu/noise';
import { d, std } from 'typegpu';

function bayer2(x: number, y: number) {
  'use gpu';
  let value = d.f32(0);
  if (y < 0.5) {
    if (x > 0.5) value = 2;
  } else {
    value = 3;
    if (x > 0.5) value = 1;
  }
  return value;
}

function bayer8(pixel: d.v2f) {
  'use gpu';
  const cell = pixel.sub(std.floor(pixel.div(8)).mul(8));
  const lowBits = cell.sub(std.floor(cell.div(2)).mul(2));
  const middleBits = std.floor(cell.div(2)).sub(std.floor(cell.div(4)).mul(2));
  const highBits = std.floor(cell.div(4));
  return (
    (bayer2(lowBits.x, lowBits.y) * 16 +
      bayer2(middleBits.x, middleBits.y) * 4 +
      bayer2(highBits.x, highBits.y) +
      0.5) /
    64
  );
}

function palettePositionAt(uv: d.v2f, aspect: number, time: number) {
  'use gpu';
  const position = uv.sub(0.5).mul(d.vec2f(aspect, 1)).mul(3.15);
  const drift = d.vec2f(time * 0.022, time * -0.055);
  const warp = d.vec2f(
    perlin2d.sample(position.mul(0.51).add(drift)),
    perlin2d.sample(position.mul(0.51).add(d.vec2f(6.3, -8.1)).sub(drift)),
  );
  const warped = position.add(warp.mul(0.52));
  let field = perlin2d.sample(warped.mul(0.74).add(drift)) * 0.57;
  field += perlin2d.sample(warped.mul(1.52).sub(drift.mul(1.7))) * 0.285;
  field += perlin2d.sample(warped.mul(3.08).add(drift.mul(2.25))) * 0.14;
  return std.clamp(std.smoothstep(-0.66, 0.67, field) * 4.22 - 0.1, 0, 4);
}

function paletteColor(level: number) {
  'use gpu';
  const ink = d.vec3f(0.027, 0.034, 0.055);
  const forest = d.vec3f(0.055, 0.255, 0.17);
  const citron = d.vec3f(0.67, 0.66, 0.16);
  const coral = d.vec3f(0.89, 0.265, 0.105);
  const paper = d.vec3f(0.94, 0.84, 0.62);
  let color = std.mix(ink, forest, std.step(0.5, level));
  color = std.mix(color, citron, std.step(1.5, level));
  color = std.mix(color, coral, std.step(2.5, level));
  color = std.mix(color, paper, std.step(3.5, level));
  return d.vec3f(color);
}

function ditheredPalette(position: number, pixel: d.v2f) {
  'use gpu';
  const threshold = bayer8(std.floor(pixel.div(3)));
  const level = std.floor(position) + std.step(threshold, std.fract(position));
  return paletteColor(level);
}

function paperMixAt(uv: d.v2f, pixel: d.v2f) {
  'use gpu';
  const broadWarp = perlin2d.sample(d.vec2f(uv.x * 3.7 + 3.4, uv.y * 1.3 + 10.7)) * 0.045;
  const fineWarp = perlin2d.sample(d.vec2f(uv.x * 11.3 - 1.7, uv.y * 2.4 + 4.1)) * 0.018;
  const threshold = bayer8(std.floor(pixel.div(3)));
  const cellJitter = (threshold - 0.5) * 0.07;
  const paperMix = std.smoothstep(0.54, 0.98, uv.y + broadWarp + fineWarp + cellJitter);
  const bottomSeal = std.smoothstep(0.94, 1, uv.y);
  return paperMix + (1 - paperMix) * bottomSeal;
}

/** The cached-Perlin chromatic disturbance adapted from the TypeGPU noise prototype. */
export const ditheredChromaticDisturbance = (
  uv: d.v2f,
  pixel: d.v2f,
  cursor: d.v2f,
  velocity: d.v2f,
  time: number,
  aspect: number,
  trailInfluence: number,
  paper: d.v3f,
): d.v3f => {
  'use gpu';

  const position = uv.sub(0.5).mul(d.vec2f(aspect, 1));
  const cursorPosition = cursor.sub(0.5).mul(d.vec2f(aspect, 1));
  const distanceToCursor = std.distance(position, cursorPosition);
  const speed = std.length(velocity.mul(d.vec2f(aspect, 1)));
  const cursorReveal = 1 - std.smoothstep(0.04, 0.42, distanceToCursor);
  const motion = std.smoothstep(0.001, 0.035, speed);
  const disturbance = std.max(cursorReveal * motion, trailInfluence);
  const direction = velocity.div(std.max(std.length(velocity), 0.0001));
  const channelOffset = direction.mul(0.008 + std.min(speed, 0.08) * 0.42).mul(disturbance);

  const centerField = palettePositionAt(uv, aspect, time);
  const redField = palettePositionAt(uv.add(channelOffset), aspect, time);
  const greenField = palettePositionAt(uv.sub(channelOffset.mul(0.24)), aspect, time);
  const blueField = palettePositionAt(uv.sub(channelOffset), aspect, time);
  const center = ditheredPalette(centerField, pixel);
  const red = ditheredPalette(redField, pixel.add(channelOffset.mul(180)));
  const green = ditheredPalette(greenField, pixel.sub(channelOffset.mul(42)));
  const blue = ditheredPalette(blueField, pixel.sub(channelOffset.mul(180)));
  const separated = d.vec3f(red.r, green.g, blue.b);
  const revealed = std.mix(center, separated, disturbance);
  const luminance = std.dot(center, d.vec3f(0.2126, 0.7152, 0.0722));
  const monochrome = d.vec3f(luminance * 0.86 + 0.018);
  const disturbanceColor = std.mix(monochrome, revealed, std.max(cursorReveal, trailInfluence));
  return std.mix(disturbanceColor, paper, paperMixAt(uv, pixel));
};
