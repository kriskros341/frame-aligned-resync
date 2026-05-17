// Verifies the Pyodide path end-to-end: boot Pyodide, load numpy, load the
// real resync_core.py, marshal fingerprints in, get a Plan back. Runs the
// SAME calls as src/core.ts, just under Node so it can be checked in CI / by
// hand. Uses the cached Korra fingerprints if present.
//
//   node verify-core.mjs
//
// Expected (cached Korra data): 73 items, speed ~0.94-0.98, target ~1444s.

import { loadPyodide } from 'pyodide';
import { readFileSync, existsSync } from 'node:fs';

const DONOR_RAW = '/tmp/cda_sig.raw';
const TARGET_RAW = '/tmp/korra_sig.raw';

if (!existsSync(DONOR_RAW) || !existsSync(TARGET_RAW)) {
  console.error(`missing fingerprint fixtures ${DONOR_RAW} / ${TARGET_RAW}`);
  console.error('regenerate with: ffmpeg -i IN -vf scale=8:8,format=gray -f rawvideo OUT');
  process.exit(1);
}

const coreSource = readFileSync(new URL('../resync_core.py', import.meta.url), 'utf8');
const loadFp = (p) => Float32Array.from(readFileSync(p)); // bytes -> floats

const donor = loadFp(DONOR_RAW);
const target = loadFp(TARGET_RAW);

console.log('booting Pyodide + numpy ...');
const py = await loadPyodide();
await py.loadPackage('numpy');
py.runPython(coreSource);

py.globals.set('_donor_flat', donor);
py.globals.set('_target_flat', target);
const meta = py.toPy({
  donor_fps: 2997 / 125,
  target_fps: 24000 / 1001,
  donor_len: 1359.296,
  target_len: 34621 / (24000 / 1001),
  arate: 44100,
});
py.globals.set('_meta', meta);

const proxy = py.runPython(`
import numpy as _np
build_plan(
    _np.asarray(_donor_flat, dtype=_np.float32).reshape(-1, 64),
    _np.asarray(_target_flat, dtype=_np.float32).reshape(-1, 64),
    _meta['donor_fps'], _meta['target_fps'],
    _meta['donor_len'], _meta['target_len'], _meta['arate'])
`);
const plan = proxy.toJs({ dict_converter: Object.fromEntries });
proxy.destroy();
meta.destroy();

const segs = plan.items.filter((i) => i.kind === 'segment');
const sils = plan.items.filter((i) => i.kind === 'silence');
const speeds = segs.map((s) => s.speed);

console.log(`target_duration : ${plan.target_duration.toFixed(2)}s`);
console.log(`pitch_mode      : ${plan.pitch_mode}`);
console.log(`items           : ${plan.items.length}  (${segs.length} segments, ${sils.length} silences)`);
console.log(`speed range     : ${Math.min(...speeds).toFixed(4)} .. ${Math.max(...speeds).toFixed(4)}`);
console.log(`silence inserts : ${sils.map((s) => s.duration.toFixed(2)).join(', ')}`);

const ok = plan.items.length > 0 && segs.length > 0 &&
  speeds.every((s) => s > 0.5 && s < 2.0);
console.log(ok ? '\nOK — Pyodide core path verified.' : '\nFAIL — implausible plan.');
process.exit(ok ? 0 : 1);
