// Data contracts shared across the pipeline. The Plan mirrors SPEC.md §5
// exactly -- it is what the algorithm core (Python, via Pyodide) emits and
// what the executor consumes.

/** The algorithm's sole output. See SPEC.md §5. */
export interface Plan {
  /** total output length, seconds */
  target_duration: number;
  /** Hz, taken from the donor audio */
  audio_sample_rate: number;
  pitch_mode: 'preserve' | 'correct';
  /** audio pieces in target-timeline order; concatenating them = full track */
  items: PlanItem[];
}

export type PlanItem = Silence | Segment;

/** A run of digital silence (donor has no footage here). */
export interface Silence {
  kind: 'silence';
  /** seconds */
  duration: number;
}

/** A stretched slice of donor audio placed on the output timeline. */
export interface Segment {
  kind: 'segment';
  /** seconds into the donor audio */
  donor_start: number;
  /** seconds into the donor audio */
  donor_end: number;
  /** seconds this piece occupies on the output */
  target_duration: number;
  /** (donor_end - donor_start) / target_duration; <1 = slowed down */
  speed: number;
}

/**
 * Per-frame video fingerprints. See SPEC.md §Stage 1: one 8x8 grayscale
 * thumbnail per frame, zero-meaned. Stored flat -- frame `f` occupies
 * `data[f*64 .. f*64+64)`.
 */
export interface Fingerprints {
  data: Float32Array;
  frameCount: number;
  /** exact frame rate, e.g. 24000/1001 */
  fps: number;
  /** seconds */
  duration: number;
  /** Hz; relevant for the donor only (0 when not needed) */
  audioSampleRate: number;
}
