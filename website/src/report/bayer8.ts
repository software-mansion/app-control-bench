import tgpu, { d, std } from 'typegpu';

const bayerDigit = tgpu.fn([d.f32, d.f32], d.f32)((x, y) => {
  'use gpu';
  return x * 2 + y * 3 - x * y * 4;
});

export const bayer8 = tgpu.fn([d.vec2f], d.f32)((cell) => {
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
