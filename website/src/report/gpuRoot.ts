import tgpu from 'typegpu';

let rootPromise: ReturnType<typeof tgpu.init> | undefined;

export function getReportGpuRoot() {
  rootPromise ??= tgpu.init();
  return rootPromise;
}
