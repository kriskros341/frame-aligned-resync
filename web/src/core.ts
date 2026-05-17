// Pyodide bridge to the algorithm core.
//
// resync_core.py (SPEC.md Stages 2-9) is the single source of truth. Rather
// than reimplement it in TypeScript, we run that exact Python in the browser
// via Pyodide. This module is the only place TS and Python meet.

import { loadPyodide, type PyodideInterface } from 'pyodide';
import type { Fingerprints, Plan } from './types';

export interface Core {
  /** SPEC.md Stages 2-9: fingerprints -> Plan. Runs resync_core.build_plan. */
  buildPlan(
    donor: Fingerprints,
    target: Fingerprints,
    pitchMode: 'preserve' | 'correct',
  ): Plan;
}

export interface LoadCoreOptions {
  /**
   * Where Pyodide fetches its runtime (.wasm/.data) assets. Omit in Node —
   * it uses the bundled node_modules copy. In the browser, set this to the
   * jsdelivr path for the matching Pyodide version.
   */
  indexURL?: string;
}

/**
 * Boot Pyodide, load numpy, and load the resync algorithm core (the contents
 * of resync_core.py, passed in as `coreSource`). Returns a handle whose
 * `buildPlan` runs the unmodified Python core.
 */
export async function loadCore(
  coreSource: string,
  opts: LoadCoreOptions = {},
): Promise<Core> {
  const pyodide: PyodideInterface = await loadPyodide(
    opts.indexURL ? { indexURL: opts.indexURL } : {},
  );
  await pyodide.loadPackage('numpy');
  pyodide.runPython(coreSource); // defines build_plan() in the __main__ namespace

  return {
    buildPlan(donor, target, pitchMode) {
      // Marshal the flat Float32 fingerprint buffers into the Python side;
      // numpy reshapes them to (N, 64) there.
      pyodide.globals.set('_donor_flat', donor.data);
      pyodide.globals.set('_target_flat', target.data);
      const meta = pyodide.toPy({
        donor_fps: donor.fps,
        target_fps: target.fps,
        donor_len: donor.duration,
        target_len: target.duration,
        arate: donor.audioSampleRate,
        pitch: pitchMode,
      });
      pyodide.globals.set('_meta', meta);

      const proxy = pyodide.runPython(`
import numpy as _np
build_plan(
    _np.asarray(_donor_flat, dtype=_np.float32).reshape(-1, 64),
    _np.asarray(_target_flat, dtype=_np.float32).reshape(-1, 64),
    _meta['donor_fps'], _meta['target_fps'],
    _meta['donor_len'], _meta['target_len'], _meta['arate'],
    pitch_mode=_meta['pitch'])
`);
      const plan = proxy.toJs({ dict_converter: Object.fromEntries }) as Plan;
      proxy.destroy();
      meta.destroy();
      return plan;
    },
  };
}
