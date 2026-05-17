"""
resync_core.py -- the frame-aligned-resync algorithm core.

Pure, I/O-free implementation of SPEC.md Stages 2-9: frame fingerprints in,
a Plan (SPEC.md section 5) out. No ffmpeg, no file access, no subprocess --
so the exact same module runs natively (the CLI executor) and in the browser
via Pyodide. This file is the single source of truth for the algorithm; it
must conform to SPEC.md.

Requires: numpy.
"""
import numpy as np

# --- parameters (SPEC.md section 3) ---
SIG        = 8       # fingerprint is SIG x SIG grayscale
WIN        = 384     # correlation window, donor frames
HOP        = 24      # step between windows, donor frames
SEARCH     = 2800    # half-width of target search range, frames
CORR_MIN   = 0.90    # min correlation for a trusted map point
OUTLIER    = 0.60    # max |residual - local median| to keep a point (s)
GAP_THRESH = 1.50    # residual jump that counts as a hard cut (s)
SEG_TRIM   = 1.50    # per-segment residual rejection band (s)
SUB        = 20.0    # sub-segment length for wander tracking (s)
SIL_EPS    = 0.003   # min gap worth inserting as silence (s)
TAIL_EPS   = 0.01    # min trailing gap worth padding (s)


def _zero_mean(fp):
    """Stage 1 tail: zero-mean each frame's fingerprint. fp: (N, SIG*SIG)."""
    a = np.asarray(fp, dtype=np.float32)
    return a - a.mean(axis=1, keepdims=True)


def correlation_map(donor, target, donor_fps, target_fps):
    """SPEC.md Stage 2 -- windowed normalized cross-correlation.

    donor, target : zero-meaned (N, SIG*SIG) fingerprint arrays.
    Returns an (M, 3) array of (donor_time, target_time, correlation).
    """
    tcum = np.concatenate(([0.0], np.cumsum((target ** 2).sum(axis=1))))
    pts = []
    for s in range(0, len(donor) - WIN, HOP):
        seg = donor[s:s + WIN]
        sn = np.sqrt((seg ** 2).sum())
        lo = max(0, s - SEARCH)
        hi = min(len(target), s + WIN + SEARCH)
        reg = target[lo:hi]
        if len(reg) < WIN:
            continue
        nfft = 1
        while nfft < len(reg) + WIN:
            nfft <<= 1
        # numerator: cross-correlation of region with seg, summed over columns
        cc = np.fft.irfft(np.fft.rfft(reg, nfft, axis=0) *
                          np.fft.rfft(seg[::-1], nfft, axis=0), nfft, axis=0)
        corr = cc[WIN - 1:WIN - 1 + len(reg) - WIN + 1].sum(axis=1)
        # denominator: per-window target energy via prefix sum
        we = np.sqrt(np.maximum(
            tcum[lo + WIN:lo + WIN + len(corr)] - tcum[lo:lo + len(corr)], 1e-9))
        nc = corr / (we * sn + 1e-9)
        b = int(np.argmax(nc))
        pts.append(((s + WIN / 2) / donor_fps,
                    (lo + b + WIN / 2) / target_fps, nc[b]))
    return np.array(pts)


def _derive_ratio(ct, kt):
    """SPEC.md Stage 4 -- robust target/donor speed ratio."""
    slopes = []
    for i in range(len(ct)):
        j = int(np.searchsorted(ct, ct[i] + 30))
        if j < len(ct) and ct[j] - ct[i] > 5:
            slopes.append((kt[j] - kt[i]) / (ct[j] - ct[i]))
    return float(np.median(slopes))


def build_plan(donor_fp, target_fp, donor_fps, target_fps,
               donor_audio_len, target_video_len, audio_sample_rate,
               pitch_mode='preserve', sub=SUB):
    """SPEC.md Stages 2-9: fingerprints -> Plan (SPEC.md section 5).

    donor_fp, target_fp : raw (N, SIG*SIG) fingerprint arrays (0-255 ints).
    Returns a Plan dict, JSON-serializable.
    """
    donor = _zero_mean(donor_fp)
    target = _zero_mean(target_fp)

    # Input sanity -- fail legibly instead of with a cryptic numpy error.
    if donor.ndim != 2 or target.ndim != 2:
        raise ValueError(
            f'fingerprints must be 2-D (N, {SIG * SIG}); got donor '
            f'{donor.shape}, target {target.shape}')
    if len(donor) <= WIN or len(target) <= WIN:
        raise ValueError(
            f'too few frames to align: donor {len(donor)}, target '
            f'{len(target)} (need > {WIN}). The decode stage produced no / '
            f'too few fingerprints -- check the console for demux/decode errors.')

    # Stage 2-3: correlation map -> confident points, sorted by donor time
    m = correlation_map(donor, target, donor_fps, target_fps)
    sel = m[:, 2] > CORR_MIN
    ct, kt = m[sel, 0], m[sel, 1]
    order = np.argsort(ct)
    ct, kt = ct[order], kt[order]
    if len(ct) < 20:
        allc = m[:, 2]
        raise ValueError(
            f'too few confident matches: {len(ct)} of {len(m)} windows above '
            f'{CORR_MIN} (max correlation {allc.max():.3f}; >0.7: '
            f'{int((allc > 0.7).sum())}, >0.5: {int((allc > 0.5).sum())}). '
            f'A low max means the fingerprints are not matching at all '
            f'(a decode problem); a high max would mean the clip is too short.')

    # Stage 4: global speed ratio
    ratio = _derive_ratio(ct, kt)

    # Stage 5: outlier rejection vs a local-median residual baseline
    res = kt - ratio * ct
    med = np.array([np.median(res[max(0, i - 15):i + 16])
                    for i in range(len(res))])
    keep = np.abs(res - med) < OUTLIER
    ct, kt = ct[keep], kt[keep]

    # Stage 6: hard-cut detection (residual jumps)
    res = kt - ratio * ct
    d = np.diff(res)
    gi = [i for i in range(len(d)) if d[i] > GAP_THRESH]
    groups = []
    for i in gi:
        if groups and i - groups[-1][-1] <= 4:
            groups[-1].append(i)
        else:
            groups.append([i])
    gaps = [(g[0], g[-1] + 1) for g in groups]

    # Stage 7: per-segment residual mappers
    segs, st = [], 0
    for i0, i1 in gaps:
        segs.append((st, i0 + 1)); st = i1
    segs.append((st, len(ct)))
    cuts = [(ct[i0] + ct[i1]) / 2 for i0, i1 in gaps]
    bounds = [0.0] + cuts + [donor_audio_len]

    seg_pts = []
    for si, (a, b) in enumerate(segs):
        cs = ct[a:b].copy()
        rs = kt[a:b] - ratio * cs
        ok = np.abs(rs - np.median(rs)) < SEG_TRIM
        cs, rs = cs[ok], rs[ok]
        sm = np.array([np.median(rs[max(0, i - 2):i + 3])
                       for i in range(len(rs))])
        seg_pts.append((np.concatenate(([bounds[si]], cs, [bounds[si + 1]])),
                        np.concatenate(([sm[0]], sm, [sm[-1]]))))

    def fmap(si, x):
        cs, ar = seg_pts[si]
        return ratio * x + float(np.interp(x, cs, ar))

    # Stage 8: sub-segment plan
    pieces = []
    for si in range(len(segs)):
        c_lo, c_hi = bounds[si], bounds[si + 1]
        n = max(1, int(round((c_hi - c_lo) / sub)))
        bps = np.linspace(c_lo, c_hi, n + 1)
        kor = list(np.maximum.accumulate([fmap(si, x) for x in bps]))
        for j in range(n):
            pieces.append((bps[j], bps[j + 1], kor[j], kor[j + 1]))

    # Stage 9: assemble the target timeline -> Plan items
    items, cursor = [], 0.0
    for c0, c1, k0, k1 in pieces:
        if k0 - cursor > SIL_EPS:
            items.append({'kind': 'silence', 'duration': float(k0 - cursor)})
        kdur = k1 - k0
        items.append({'kind': 'segment',
                      'donor_start': float(c0), 'donor_end': float(c1),
                      'target_duration': float(kdur),
                      'speed': float((c1 - c0) / kdur)})
        cursor = k1
    if target_video_len - cursor > TAIL_EPS:
        items.append({'kind': 'silence',
                      'duration': float(target_video_len - cursor)})

    return {'target_duration': float(target_video_len),
            'audio_sample_rate': int(audio_sample_rate),
            'pitch_mode': pitch_mode,
            'items': items}
