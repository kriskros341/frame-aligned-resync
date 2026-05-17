// Verifies the pure audio assembly (src/audio.ts) -- no browser needed.
// Run: node verify-audio.mjs   (Node strips the TS types on import)

import { assemblePcm, resampleLinear } from './src/audio.ts';

let ok = true;
const check = (name, cond) => {
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}`);
  if (!cond) ok = false;
};

// resampleLinear: ramp 0..3 stretched to 7 samples -> 0,0.5,1,...,3
const r = resampleLinear(Float32Array.from([0, 1, 2, 3]), 7);
check('resampleLinear length', r.length === 7);
check('resampleLinear endpoints', r[0] === 0 && Math.abs(r[6] - 3) < 1e-6);
check('resampleLinear midpoint', Math.abs(r[3] - 1.5) < 1e-6);

// assemblePcm: 1s segment (donor 0..1s) + 1s silence, at 10 Hz -> 20 samples
const sr = 10;
const donor = [Float32Array.from({ length: 30 }, (_, i) => i + 1)]; // 3s of ramp
const plan = {
  target_duration: 2,
  audio_sample_rate: sr,
  pitch_mode: 'correct',
  items: [
    { kind: 'segment', donor_start: 0, donor_end: 1, target_duration: 1, speed: 1 },
    { kind: 'silence', duration: 1 },
  ],
};
const [out] = assemblePcm(plan, donor, sr);
check('assemblePcm total length', out.length === 20);
check('assemblePcm segment filled', out[0] !== 0 && out[5] !== 0);
check('assemblePcm silence is zero', out.slice(10).every((v) => v === 0));

// a stretched segment: donor 0..1s (10 samples) -> 2s target (20 samples)
const plan2 = {
  target_duration: 2, audio_sample_rate: sr, pitch_mode: 'correct',
  items: [{ kind: 'segment', donor_start: 0, donor_end: 1, target_duration: 2, speed: 0.5 }],
};
const [out2] = assemblePcm(plan2, donor, sr);
check('assemblePcm stretch length', out2.length === 20);
check('assemblePcm stretch monotonic', out2[0] < out2[10] && out2[10] < out2[19]);

console.log(ok ? '\nOK -- audio assembly verified.' : '\nFAIL');
process.exit(ok ? 0 : 1);
