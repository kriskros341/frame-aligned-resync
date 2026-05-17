// Orchestrates the resync pipeline.
//
//   extractFingerprints  -- WebCodecs decode            (decode.ts)
//   buildPlan            -- SPEC Stages 2-9, Python core (Pyodide, core.ts)
//   executePlan          -- assemble audio + mux        (execute.ts)

import type { Fingerprints, Plan } from './types';
import { loadCore, type Core } from './core';
// resync_core.py embedded at build time -- the single source of truth.
import coreSource from '../../resync_core.py?raw';
import { extractFingerprints } from './decode';
import { executePlan } from './execute';

/** Pyodide runtime assets -- must match the installed `pyodide` package. */
const PYODIDE_INDEX_URL = 'https://cdn.jsdelivr.net/pyodide/v0.29.4/full/';

/** Reports progress: a stage label and a 0..1 fraction (NaN = indeterminate). */
export type Progress = (stage: string, fraction: number) => void;

// The Pyodide core is heavy to boot, so load it once and reuse it.
let corePromise: Promise<Core> | null = null;
function getCore(): Promise<Core> {
  if (!corePromise) {
    corePromise = loadCore(coreSource, { indexURL: PYODIDE_INDEX_URL });
  }
  return corePromise;
}

/**
 * SPEC.md Stages 2-9 -- run the Python algorithm core via Pyodide.
 *
 * The web build uses pitch_mode 'correct' (resample): execute.ts realises the
 * Plan by plain resampling, so no WASM time-stretcher is needed. (The CLI
 * defaults to 'preserve'/atempo.)
 */
export async function buildPlan(
  donor: Fingerprints,
  target: Fingerprints,
): Promise<Plan> {
  const core = await getCore();
  return core.buildPlan(donor, target, 'correct');
}

/** Full pipeline: donor + target files -> resynced output. */
export async function resync(
  donor: File,
  target: File,
  onProgress: Progress,
): Promise<Blob> {
  onProgress('fingerprinting donor video', NaN);
  const donorFp = await extractFingerprints(donor, onProgress);

  onProgress('fingerprinting target video', NaN);
  const targetFp = await extractFingerprints(target, onProgress);

  onProgress('building retiming plan (booting Python core)', NaN);
  const plan = await buildPlan(donorFp, targetFp);

  onProgress('rendering output', NaN);
  return executePlan(plan, donor, target);
}
