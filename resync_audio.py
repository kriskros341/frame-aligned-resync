#!/usr/bin/env python3
"""
resync_audio.py  --  transplant an audio track between two copies of the
same video that have drifted out of sync (different cuts / PAL speed-up / etc.)

It does NOT rely on the audio at all -- the two language dubs can't be
correlated directly. Instead it fingerprints the *video frames* (identical
picture in both files) to build an exact time-map, then retimes the donor
audio onto the target video's timeline: speed-matched, wander-corrected,
and with silence inserted wherever the donor is simply missing footage.

Usage:
    python3 resync_audio.py AUDIO_SRC VIDEO_SRC OUTPUT [options]

    AUDIO_SRC : file whose audio you want to keep   (e.g. the Polish dub)
    VIDEO_SRC : file whose video you want to keep   (e.g. the HD BluRay)
    OUTPUT    : output file (.mp4 / .mkv)

Options:
    --pitch-correct   resample instead of time-stretch (undoes PAL pitch
                      shift; default keeps the donor audio's original pitch)
    --sub SEC         sub-segment length for wander tracking   (default 20)
    --abitrate RATE   output audio bitrate                     (default 256k)
    --keep-temp       keep the intermediate fingerprint files

Requires: ffmpeg, ffprobe, numpy.
"""
import argparse, subprocess, sys, tempfile, os, json
import numpy as np

SIG = 8                       # frame fingerprint is SIG x SIG grayscale
CORR_MIN = 0.90               # minimum correlation to trust a map point
GAP_THRESH = 1.5              # residual jump (s) that counts as a hard cut


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd[:3])}...\n{p.stderr[-800:]}")
    return p.stdout


def probe(path):
    """Return (video_fps, n_frames_estimate, audio_duration, audio_rate)."""
    out = run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
               '-of', 'json', path])
    info = json.loads(out)
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    a = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    num, den = v['r_frame_rate'].split('/')
    fps = float(num) / float(den)
    dur = float(info['format']['duration'])
    arate = int(a['sample_rate'])
    return fps, dur, arate


def extract_sigs(path, raw_path):
    """Decode the video to a stream of SIGxSIG grayscale frame fingerprints."""
    run(['ffmpeg', '-y', '-v', 'error', '-i', path,
         '-an', '-vf', f'scale={SIG}:{SIG},format=gray',
         '-f', 'rawvideo', raw_path])
    x = np.fromfile(raw_path, dtype=np.uint8).astype(np.float32)
    n = len(x) // (SIG * SIG)
    a = x[:n * SIG * SIG].reshape(n, SIG * SIG)
    return a - a.mean(axis=1, keepdims=True)        # zero-mean each frame


def build_map(don, tgt, don_fps, tgt_fps, win=384, hop=24, search=2800):
    """Windowed normalized cross-correlation: donor frame window -> target.
    Returns array of (donor_time, target_time, correlation)."""
    tgt_e = (tgt ** 2).sum(axis=1)
    tcum = np.concatenate(([0.0], np.cumsum(tgt_e)))
    pts = []
    for s in range(0, len(don) - win, hop):
        seg = don[s:s + win]
        sn = np.sqrt((seg ** 2).sum())
        lo = max(0, s - search)
        hi = min(len(tgt), s + win + search)
        reg = tgt[lo:hi]
        if len(reg) < win:
            continue
        nfft = 1
        while nfft < len(reg) + win:
            nfft <<= 1
        cc = np.fft.irfft(np.fft.rfft(reg, nfft, axis=0) *
                          np.fft.rfft(seg[::-1], nfft, axis=0), nfft, axis=0)
        corr = cc[win - 1:win - 1 + len(reg) - win + 1].sum(axis=1)
        we = np.sqrt(np.maximum(
            tcum[lo + win:lo + win + len(corr)] - tcum[lo:lo + len(corr)], 1e-9))
        nc = corr / (we * sn + 1e-9)
        b = int(np.argmax(nc))
        pts.append(((s + win / 2) / don_fps,
                    (lo + b + win / 2) / tgt_fps, nc[b]))
    return np.array(pts)


def derive_ratio(ct, kt):
    """Robust target/donor speed ratio (median of local slopes, ~30s apart)."""
    slopes = []
    for i in range(len(ct)):
        j = np.searchsorted(ct, ct[i] + 30)
        if j < len(ct) and ct[j] - ct[i] > 5:
            slopes.append((kt[j] - kt[i]) / (ct[j] - ct[i]))
    return float(np.median(slopes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio_src'); ap.add_argument('video_src')
    ap.add_argument('output')
    ap.add_argument('--pitch-correct', action='store_true')
    ap.add_argument('--sub', type=float, default=20.0)
    ap.add_argument('--abitrate', default='256k')
    ap.add_argument('--keep-temp', action='store_true')
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='resync_')
    don_raw, tgt_raw = f'{tmp}/don.raw', f'{tmp}/tgt.raw'

    print('[1/5] probing files...')
    don_fps, don_audio_len, arate = probe(args.audio_src)
    tgt_fps, tgt_video_len, _ = probe(args.video_src)
    print(f'      donor : {don_fps:.4f} fps, audio {don_audio_len:.1f}s')
    print(f'      target: {tgt_fps:.4f} fps, video {tgt_video_len:.1f}s')

    print('[2/5] extracting frame fingerprints (decodes both videos once)...')
    don = extract_sigs(args.audio_src, don_raw)
    tgt = extract_sigs(args.video_src, tgt_raw)

    print('[3/5] cross-correlating video to build the time-map...')
    m = build_map(don, tgt, don_fps, tgt_fps)
    sel = m[:, 2] > CORR_MIN
    ct, kt = m[sel, 0], m[sel, 1]
    order = np.argsort(ct); ct, kt = ct[order], kt[order]
    if len(ct) < 20:
        sys.exit('too few confident matches -- are these the same video?')
    ratio = derive_ratio(ct, kt)
    print(f'      {len(ct)} confident points, speed ratio = {ratio:.5f}')

    # reject outliers vs a local-median residual baseline
    res = kt - ratio * ct
    med = np.array([np.median(res[max(0, i - 15):i + 16])
                    for i in range(len(res))])
    keep = np.abs(res - med) < 0.6
    ct, kt = ct[keep], kt[keep]

    # detect hard cuts (residual jumps)
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
    print(f'      {len(gaps)} hard cut(s) detected')

    # split into macro-segments at the cuts
    segs, st = [], 0
    for i0, i1 in gaps:
        segs.append((st, i0 + 1)); st = i1
    segs.append((st, len(ct)))
    cuts = [(ct[i0] + ct[i1]) / 2 for i0, i1 in gaps]
    bounds = [0.0] + cuts + [don_audio_len]

    # per-segment mapper: target = ratio*donor + smoothed residual
    seg_pts = []
    for si, (a, b) in enumerate(segs):
        cs = ct[a:b].copy()
        rs = kt[a:b] - ratio * cs
        ok = np.abs(rs - np.median(rs)) < 1.5
        cs, rs = cs[ok], rs[ok]
        sm = np.array([np.median(rs[max(0, i - 2):i + 3])
                       for i in range(len(rs))])
        seg_pts.append((np.concatenate(([bounds[si]], cs, [bounds[si + 1]])),
                        np.concatenate(([sm[0]], sm, [sm[-1]]))))

    def fmap(si, x):
        cs, ar = seg_pts[si]
        return ratio * x + float(np.interp(x, cs, ar))

    print('[4/5] building retiming filter graph...')
    pieces = []
    for si in range(len(segs)):
        c_lo, c_hi = bounds[si], bounds[si + 1]
        n = max(1, int(round((c_hi - c_lo) / args.sub)))
        bps = np.linspace(c_lo, c_hi, n + 1)
        kor = list(np.maximum.accumulate([fmap(si, x) for x in bps]))
        for j in range(n):
            pieces.append((bps[j], bps[j + 1], kor[j], kor[j + 1]))

    # assemble target timeline: silence fills + retimed pieces
    timeline, cursor = [], 0.0
    for c0, c1, k0, k1 in pieces:
        if k0 - cursor > 0.003:
            timeline.append(('sil', k0 - cursor))
        timeline.append(('seg', c0, c1, k1 - k0))
        cursor = k1
    if tgt_video_len - cursor > 0.01:
        timeline.append(('sil', tgt_video_len - cursor))

    lines, labels = [], []
    for n, item in enumerate(timeline):
        lab = f'p{n}'
        if item[0] == 'sil':
            lines.append(f'aevalsrc=0|0:d={item[1]:.6f}:s={arate}[{lab}];')
        else:
            _, c0, c1, kdur = item
            speed = (c1 - c0) / kdur          # <1 == slow down
            if args.pitch_correct:            # resample: changes pitch
                eff = int(arate / speed)
                stretch = (f'asetrate={arate}*{speed:.9f},'
                           f'aresample={arate}')
            else:                              # atempo: keeps pitch
                stretch = f'atempo={speed:.9f}'
            lines.append(
                f'[0:a]atrim={c0:.6f}:{c1:.6f},asetpts=N/SR/TB,{stretch},'
                f'atrim=end={kdur:.6f},apad=whole_dur={kdur:.6f},'
                f'asetpts=N/SR/TB[{lab}];')
        labels.append(f'[{lab}]')
    lines.append(''.join(labels) + f'concat=n={len(labels)}:v=0:a=1[aout]')
    filt = f'{tmp}/filter.txt'
    open(filt, 'w').write('\n'.join(lines))

    nseg = sum(1 for t in timeline if t[0] == 'seg')
    print(f'      {nseg} sub-segments, '
          f'{sum(1 for t in timeline if t[0]=="sil")} silence insert(s)')

    print('[5/5] muxing output (video copied, audio re-encoded)...')
    run(['ffmpeg', '-y', '-v', 'warning',
         '-i', args.audio_src, '-i', args.video_src,
         '-filter_complex_script', filt,
         '-map', '1:v:0', '-map', '[aout]',
         '-c:v', 'copy', '-c:a', 'aac', '-b:a', args.abitrate,
         '-ar', str(arate), '-movflags', '+faststart', args.output])

    if not args.keep_temp:
        for f in (don_raw, tgt_raw, filt):
            try: os.remove(f)
            except OSError: pass
        try: os.rmdir(tmp)
        except OSError: pass
    else:
        print(f'      temp files kept in {tmp}')
    print(f'\nDone -> {args.output}')


if __name__ == '__main__':
    main()
