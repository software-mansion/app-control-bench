/**
 * Set by both Vite configs from whether `web/index-telemetry.html` is in the tree - the single signal
 * that decides whether this build has a telemetry screen. Replaced by a literal at build time, so the
 * public build eliminates the branches it guards rather than shipping a link to a page it lacks.
 */
declare const __HAS_TELEMETRY__: boolean;
