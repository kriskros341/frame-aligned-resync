// Pure audio assembly -- the verifiable heart of executePlan (SPEC.md §7).
// No browser APIs here, so it can be unit-tested under Node.

import type { Plan } from './types';

/** Linear-resample one channel of `src` to exactly `outLen` samples. */
export function resampleLinear(src: Float32Array, outLen: number): Float32Array {
  const out = new Float32Array(outLen);
  if (outLen === 0) return out;
  if (src.length === 0) return out;
  if (src.length === 1) {
    out.fill(src[0]);
    return out;
  }
  const ratio = (src.length - 1) / Math.max(1, outLen - 1);
  for (let i = 0; i < outLen; i++) {
    const x = i * ratio;
    const i0 = Math.floor(x);
    const i1 = Math.min(i0 + 1, src.length - 1);
    const f = x - i0;
    out[i] = src[i0] * (1 - f) + src[i1] * f;
  }
  return out;
}

/**
 * Realize a Plan into output PCM (SPEC.md §7).
 *
 * This is the resample path -- it stretches each donor segment to its target
 * length, which also shifts pitch. That matches `pitch_mode: 'correct'`.
 * (Pitch-preserving 'preserve' mode would need a WASM time-stretcher here.)
 *
 * `donor` is one Float32Array per channel. Returns the output channels,
 * each `round(plan.target_duration * sampleRate)` samples long.
 */
export function assemblePcm(
  plan: Plan,
  donor: Float32Array[],
  sampleRate: number,
): Float32Array[] {
  const totalOut = Math.round(plan.target_duration * sampleRate);
  const out = donor.map(() => new Float32Array(totalOut));
  let cursor = 0; // output sample index

  for (const item of plan.items) {
    if (item.kind === 'silence') {
      cursor += Math.round(item.duration * sampleRate);
      continue;
    }
    const outLen = Math.round(item.target_duration * sampleRate);
    const s0 = Math.round(item.donor_start * sampleRate);
    const s1 = Math.round(item.donor_end * sampleRate);
    const room = Math.max(0, Math.min(outLen, totalOut - cursor));
    for (let c = 0; c < donor.length; c++) {
      const slice = donor[c].subarray(
        Math.min(s0, donor[c].length),
        Math.min(Math.max(s0, s1), donor[c].length),
      );
      const stretched = resampleLinear(slice, outLen);
      out[c].set(stretched.subarray(0, room), cursor);
    }
    cursor += outLen;
  }
  return out;
}
