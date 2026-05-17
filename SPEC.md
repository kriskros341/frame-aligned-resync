# Frame-Aligned Resync — Algorithm Specification

This document is the **single source of truth** for the resync algorithm. Any
implementation (the Python CLI, a future TypeScript/WebCodecs port, …) must
conform to it. When the algorithm changes, change this file first, then
regenerate the implementations from it.

The spec is language-independent: it defines data, parameters, and math — not
I/O. Decoding video and writing audio are platform-specific and described only
in the non-normative "Execution" appendix.

---

## 1. Purpose

Given two video files that contain the **same picture content** but have
drifted out of sync (different frame-rate lineage, broadcast cuts, …), retime
the audio of one onto the timeline of the other. The two audio tracks may be
different languages, so they **cannot** be compared directly — alignment is
done entirely through the video frames, which are identical content.

## 2. Terminology

| Term         | Meaning |
|--------------|---------|
| **donor**    | the file whose **audio** is kept |
| **target**   | the file whose **video** is kept |
| **fingerprint** | an 8×8 grayscale thumbnail of one video frame (64 numbers) |
| **map point**   | a measured correspondence `(donor_time, target_time, correlation)` |
| **residual**    | `target_time − ratio · donor_time` (desync with the global speed removed) |
| **ratio**       | global target/donor speed ratio |
| **plan**        | the algorithm's output: a target-timeline list of audio pieces |

All times are in **seconds**. `fps` values are exact rationals (e.g. 24000/1001).

## 3. Parameters

| Name         | Default | Meaning |
|--------------|---------|---------|
| `SIG`        | 8       | fingerprint is `SIG × SIG` grayscale |
| `WIN`        | 384     | correlation window, in donor frames |
| `HOP`        | 24      | step between correlation windows, in donor frames |
| `SEARCH`     | 2800    | half-width of the target search range, in frames |
| `CORR_MIN`   | 0.90    | minimum correlation for a map point to be trusted |
| `OUTLIER`    | 0.60    | max |residual − local median| to keep a point (seconds) |
| `GAP_THRESH` | 1.50    | residual jump that counts as a hard cut (seconds) |
| `SEG_TRIM`   | 1.50    | per-segment residual rejection band (seconds) |
| `SUB`        | 20.0    | sub-segment length for wander tracking (seconds) |
| `SIL_EPS`    | 0.003   | minimum gap worth inserting as silence (seconds) |
| `TAIL_EPS`   | 0.01    | minimum trailing gap worth padding (seconds) |

## 4. Pipeline

```
decode → fingerprint → correlate → filter → derive ratio
→ reject outliers → detect gaps → fit segment mappers
→ build sub-segment plan → assemble timeline → PLAN
```

### Stage 1 — Fingerprints

For each video, decode **every** frame, scale it to `SIG × SIG` grayscale, and
read the pixels in row-major order as `SIG·SIG` integers (0–255).

For each frame, subtract the frame's own mean from its values (zero-mean per
frame). This makes the comparison ignore absolute brightness.

Result: per video, an `N × 64` real matrix `F` (N = frame count).
Call them `D` (donor) and `T` (target).

### Stage 2 — Correlation map

Slide a window over the donor and find where it best matches the target.

For each window start `s = 0, HOP, 2·HOP, …` while `s + WIN ≤ N_donor`:

1. `seg = D[s : s+WIN]`  (a `WIN × 64` block).
2. Search range in the target: `lo = max(0, s − SEARCH)`,
   `hi = min(N_target, s + WIN + SEARCH)`; `region = T[lo : hi]`.
   Skip this window if `hi − lo < WIN`.
3. For every candidate offset `t` with `lo ≤ t ≤ hi − WIN`, compute the
   **normalized cross-correlation**

   ```
   ncc(t) =  Σ_{i,c} seg[i,c] · T[t+i, c]
            ───────────────────────────────────
              ‖seg‖ · ‖T[t : t+WIN]‖   + 1e-9
   ```

   where `‖X‖ = sqrt(Σ X²)` over all elements of the block.
4. `best = argmax_t ncc(t)`.
5. Emit the map point:
   - `donor_time  = (s + WIN/2) / donor_fps`
   - `target_time = (best + WIN/2) / target_fps`
   - `correlation = ncc(best)`

**Implementation note (optional, must give identical results):** the numerator
for all `t` at once is the cross-correlation of `region` with `seg`, computable
per-column via FFT and summed over the 64 columns. The denominator's
per-window target energy `Σ T[t:t+WIN]²` is computable for all `t` from a
prefix-sum (cumulative sum) of per-frame energies. This is purely a speed
optimization; a direct double loop is equally correct.

### Stage 3 — Filter to confident points

Keep only map points with `correlation > CORR_MIN`. Sort the survivors by
`donor_time` ascending. Call the parallel arrays `ct` (donor times) and
`kt` (target times). If fewer than 20 points remain, abort — the inputs are
probably not the same content.

### Stage 4 — Speed ratio

The global target/donor speed ratio, robust to gaps:

```
slopes = []
for each i:
    j = first index with ct[j] ≥ ct[i] + 30
    if j exists and ct[j] − ct[i] > 5:
        slopes.append( (kt[j] − kt[i]) / (ct[j] − ct[i]) )
ratio = median(slopes)
```

### Stage 5 — Outlier rejection

```
res[i]      = kt[i] − ratio · ct[i]
baseline[i] = median( res[i−15 .. i+15] )         (clamped to array bounds)
keep point i  iff  |res[i] − baseline[i]| < OUTLIER
```

Drop rejected points from `ct` and `kt`.

### Stage 6 — Gap detection (hard cuts)

Recompute `res = kt − ratio·ct` on the cleaned arrays.

```
d[i] = res[i+1] − res[i]
raw  = { i : d[i] > GAP_THRESH }
```

Merge raw indices into groups: indices within 4 of each other join one group.
Each group becomes a gap `(i0, i1)` where `i0` is the group's first index and
`i1` is its last index + 1. `i0` is the last point before the cut, `i1` the
first point after.

### Stage 7 — Segment mappers

The cuts split the points into **macro-segments**. With gaps
`(i0₁,i1₁), …, (i0ₘ,i1ₘ)`:

- Segment index ranges: `(0, i0₁+1), (i1₁, i0₂+1), …, (i1ₘ, len)`.
- Cut donor-times: `cut_k = (ct[i0ₖ] + ct[i1ₖ]) / 2`.
- Donor-time bounds: `bounds = [0, cut₁, …, cutₘ, donor_audio_duration]`.
  Segment `si` spans donor time `[bounds[si], bounds[si+1]]`.

For each segment `si` with point index range `[a, b)`:

1. `cs = ct[a:b]`, `rs = kt[a:b] − ratio · cs`.
2. Reject within-segment outliers: keep entries with
   `|rs − median(rs)| < SEG_TRIM`.
3. Smooth: `sm[i] = median( rs[i−2 .. i+2] )` (clamped to bounds).
4. Build augmented arrays for interpolation, pinning the segment's endpoints:
   - `aug_x = [ bounds[si] ] ++ cs ++ [ bounds[si+1] ]`
   - `aug_r = [ sm[0] ]      ++ sm ++ [ sm[-1] ]`

The segment's **mapper** is:

```
map(si, x) = ratio · x + linear_interp(x, aug_x, aug_r)
```

`linear_interp` is standard piecewise-linear interpolation; for `x` outside
`[aug_x[0], aug_x[-1]]` it clamps to the nearest endpoint value.

### Stage 8 — Sub-segment plan

Within each segment, cut the donor span into sub-segments of ~`SUB` seconds so
the mapper's non-linear wander is followed piecewise-linearly.

```
for each segment si spanning donor [c_lo, c_hi]:
    n   = max(1, round((c_hi − c_lo) / SUB))
    bps = n+1 points evenly spaced from c_lo to c_hi
    k   = [ map(si, x) for x in bps ]
    k   = running_max(k)                     # enforce monotonic non-decreasing
    for j in 0 .. n-1:
        emit piece (donor_start = bps[j],  donor_end = bps[j+1],
                    target_start = k[j],   target_end = k[j+1])
```

### Stage 9 — Timeline assembly

Walk the pieces in order, inserting silence wherever the target jumps ahead
(the donor has no footage there), and pad the end to the full target video
length.

```
cursor = 0
items  = []
for piece in pieces:
    if piece.target_start − cursor > SIL_EPS:
        items.append( Silence(duration = piece.target_start − cursor) )
    items.append( Segment(donor_start, donor_end,
                          target_duration = piece.target_end − piece.target_start) )
    cursor = piece.target_end
if target_video_duration − cursor > TAIL_EPS:
    items.append( Silence(duration = target_video_duration − cursor) )
```

## 5. Output — the Plan

The Plan is the algorithm's sole product and the contract between the core and
any executor:

```jsonc
Plan = {
  "target_duration":   number,        // seconds; total output length
  "audio_sample_rate": number,        // Hz; taken from the donor audio
  "pitch_mode":        "preserve" | "correct",
  "items": [ Item, ... ]              // in target-timeline order
}

Item = Silence | Segment

Silence = { "kind": "silence", "duration": number }   // seconds of digital silence

Segment = {
  "kind":            "segment",
  "donor_start":     number,   // seconds into the donor audio
  "donor_end":       number,   // seconds into the donor audio
  "target_duration": number,   // seconds this piece occupies on the output
  "speed":           number    // = (donor_end − donor_start) / target_duration
}
```

Concatenating every item in order yields the full output audio track, whose
length equals `target_duration`.

`speed < 1` means the donor piece is **slowed down** (stretched); `speed > 1`
means sped up. `pitch_mode` selects how the stretch is realized (see Execution).

## 6. Invariants

- Items are contiguous on the target timeline (no overlaps, no unintended gaps).
- Each Segment's realized output **must be exactly `target_duration`** long
  (pad with silence if the stretch came up short, trim if long). This prevents
  positioning error from accumulating across pieces.
- `target_time` is non-decreasing across the whole plan (Stage 8 `running_max`).
- The donor audio is read strictly left to right; donor spans of consecutive
  segments are contiguous (the donor audio itself has no gaps — gaps exist only
  on the target side).

## 7. Execution (non-normative, platform-specific)

This part is intentionally **not** single-source — it differs per platform and
rarely changes. An executor consumes a Plan and produces the output file.

**ffmpeg (CLI port):**
- `Silence` → `aevalsrc=0|0:d=<duration>:s=<rate>`
- `Segment`, `pitch_mode = preserve` → `atempo=<speed>` (pitch-preserving)
- `Segment`, `pitch_mode = correct`  → `asetrate=<rate>*<speed>,aresample=<rate>`
  (resamples — also shifts pitch, undoing a PAL-style speed-up)
- Each segment: `atrim` the donor span, apply the stretch, then force the exact
  length with `atrim=end=<target_duration>,apad=whole_dur=<target_duration>`.
- `concat` all items; mux with the target video stream copied (`-c:v copy`).

**WebCodecs (browser port):**
- Stage 1 decoding → `VideoDecoder` (hardware), downscale each frame to 8×8,
  discard — memory stays flat.
- Stages 2–9 are pure math — port directly from this spec.
- `Silence` → a buffer of zero samples.
- `Segment` → take the donor PCM span and time-stretch by `1/speed`
  (`preserve`: a WASM time-stretcher such as SoundTouch; `correct`: plain
  resampling). Force the result to exactly `target_duration` samples.
- Concatenate, encode (`AudioEncoder`), mux with the copied target video track.
