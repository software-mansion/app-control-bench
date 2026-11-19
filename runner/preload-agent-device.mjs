#!/usr/bin/env node
// agent-device's host preload (runner/preload.py): its XCTest runner, built by xcodebuild into
// ~/.agent-device/apple-runner/derived/ios-simulator/cache-<key>/ the first time any session needs it.
//
//   node preload-agent-device.mjs build <agent-device dir> <udid>   build it through the tool's own code
//   node preload-agent-device.mjs check <agent-device dir>          exit 0 iff the tool's next run reuses it
//
// 0.21.40 (like 0.21.38 before it) has no device-free way in: `prepare ios-runner` builds the runner and then starts it on a
// device. So this calls what every session calls. `build` runs ensureXctestrunArtifact, which builds
// (or reuses) the artifact and writes the manifest the tool's next run checks; `check` asks the tool's
// own evaluateExistingXctestrun whether a cache directory is reusable for the metadata it expects now.
//
// None of the three functions is public API, and the bundle is minified: they are exported under
// one-letter names that change with every build. So each is found by what its source contains (FIND
// below) rather than by name. A build where a function is missing, or where two match, is refused.
// The runner code reaches the host through two bindings the tool's daemon makes when it composes
// itself, and this script makes the same two: the capability port (process execution, locks, the
// toolchain), bound when the tool's composition root (HOST) is loaded, and the host environment the
// runner reads its configuration from, set with the one function here that is exported by name.
//
// The tool formats the build's -destination as `platform=iOS Simulator,id=<udid>`, which xcodebuild does
// not match to its generic placeholder, so the build names a real simulator: the one
// sim_device.build_destination() picks, never the golden or a bench-* device. xcodebuild only resolves
// it; it is neither booted nor installed to. The cache key has no device in it, so the check needs none.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const VERSION = '0.21.40';
const HOST = 'dist/src/runner-client.js';
const OWNER = 'dist/src/runner-owner-facade.js';
// name -> [module, strings its minified source must contain].
const FIND = {
  ensureXctestrunArtifact: ['dist/src/runner-disposal.js', ['forceRunnerXctestrunRebuild']],
  resolveExpectedRunnerCacheMetadata: ['dist/src/runner-cache-metadata.js', ['runnerSourceFingerprint:', 'schemaVersion']],
  evaluateExistingXctestrun: ['dist/src/runner-lease.js', ['reuse_ready', 'artifact_manifest_missing']],
};
const [mode, dir, udid] = process.argv.slice(2);

function die(msg, code = 2) {
  console.error(msg);
  process.exit(code);
}

if (!dir || !(mode === 'check' || (mode === 'build' && udid))) {
  die('usage: preload-agent-device.mjs build <agent-device dir> <udid> | check <agent-device dir>');
}
const version = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8')).version;
if (version !== VERSION) {
  die(`agent-device ${version}: this preload reaches into ${VERSION}'s bundle and has not been verified ` +
      `for ${version}. Re-derive the runner build path for it and update runner/preload-agent-device.mjs.`);
}

// Importing the modules starts the tool's daemon bookkeeping; a throwaway state dir keeps it off
// ~/.agent-device, whose daemon lock a real session may hold. The runner cache is HOME-based, not
// state-dir-based, so this does not move the artifact.
const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bench-preload-adev-'));
process.env.AGENT_DEVICE_STATE_DIR = stateDir;
process.on('exit', () => fs.rmSync(stateDir, { recursive: true, force: true }));

async function load(file) {
  try {
    return await import(pathToFileURL(path.resolve(dir, file)).href);
  } catch (e) {
    die(`agent-device ${version} at ${dir}: cannot load ${file} (${e?.message ?? e}). Re-derive the runner ` +
        `build path for this build and update runner/preload-agent-device.mjs.`);
  }
}

await load(HOST);
const owner = await load(OWNER);
if (typeof owner.setAppleRunnerHostEnvironment !== 'function') {
  die(`agent-device ${version} at ${dir}: ${OWNER} exports no setAppleRunnerHostEnvironment. This build ` +
      `differs from the verified ${VERSION} bundle. Re-derive the runner build path for it and update ` +
      `runner/preload-agent-device.mjs.`);
}
owner.setAppleRunnerHostEnvironment({ env: process.env, cwd: process.cwd() });
const tool = {};
for (const [name, [file, marks]] of Object.entries(FIND)) {
  const exported = await load(file);
  const hits = Object.values(exported).filter((value) => typeof value === 'function'
    && marks.every((mark) => Function.prototype.toString.call(value).includes(mark)));
  if (hits.length !== 1) {
    die(`agent-device ${version} at ${dir}: ${hits.length} exports of ${file} look like ${name} (expected 1). ` +
        `This build differs from the verified ${VERSION} bundle. Re-derive the runner build path for it and ` +
        `update runner/preload-agent-device.mjs.`);
  }
  tool[name] = hits[0];
}

const device = { platform: 'ios', kind: 'simulator', id: udid ?? 'unused-by-the-cache-key', name: 'bench-preload', target: 'mobile', booted: false };
let expected;
try {
  expected = tool.resolveExpectedRunnerCacheMetadata(device);
} catch (e) {
  // The tool refuses to key the cache on a toolchain it could not fingerprint.
  die(`toolchain fingerprint unavailable: ${e?.message ?? e}\n${JSON.stringify(e?.details ?? {})}`, 1);
}
const unknown = ['xcodeVersion', 'xcodeBuildVersion', 'sdkVersion', 'sdkBuildVersion'].filter((k) => expected[k] === 'unknown');
if (unknown.length) die(`toolchain fingerprint incomplete (${unknown.join(', ')} unknown)`);

if (mode === 'build') {
  const logPath = path.join(stateDir, 'runner-build.log');
  // Under preload.py's 1800 s step timeout, so the tool's own timeout reaps its detached xcodebuild.
  // The build phase reads its budget as a deadline's remaining time and a cancellation signal.
  const deadlineMs = Date.now() + 25 * 60_000;
  const budget = { deadline: { remainingMs: () => deadlineMs - Date.now() }, signal: new AbortController().signal };
  try {
    const r = await tool.ensureXctestrunArtifact(device, { logPath, budget });
    console.log(JSON.stringify({ derived: r.derived, cache: r.cache, artifact: r.artifact, buildMs: r.buildMs }));
    process.exit(0);
  } catch (e) {
    const log = fs.existsSync(logPath) ? fs.readFileSync(logPath, 'utf8').split('\n').slice(-40).join('\n') : '';
    die(`${e?.message ?? e}\n${JSON.stringify(e?.details ?? {})}\n${log}`, 1);
  }
}

// The cache is keyed on the expected metadata, and a cache directory records the metadata it was built
// for, so the directory the tool's next run looks in is the one (if any) whose record the tool itself
// accepts as reusable for `expected`. An override names the single directory to ask about.
const override = process.env.AGENT_DEVICE_IOS_RUNNER_DERIVED_PATH?.trim();
const base = path.join(os.homedir(), '.agent-device', 'apple-runner', 'derived', 'ios-simulator');
let candidates = [];
if (override) {
  candidates = [path.resolve(override)];
} else {
  try {
    candidates = fs.readdirSync(base).filter((name) => /^cache-[0-9a-f]{16}$/.test(name)).map((name) => path.join(base, name));
  } catch {}
}
const misses = [];
for (const derived of candidates) {
  const state = await tool.evaluateExistingXctestrun({ derived, expectedCacheMetadata: expected });
  if (state.reason === 'reuse_ready') {
    console.log(`HIT ${derived}`);
    process.exit(0);
  }
  misses.push(`${path.basename(derived)}: ${state.reason}`);
}
console.log(`MISS ${override ? path.resolve(override) : base}: ${misses.join('; ') || 'no runner cache'}`);
process.exit(1);
