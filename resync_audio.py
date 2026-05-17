#!/usr/bin/env python3
"""
resync_audio.py -- CLI executor for frame-aligned-resync.

Transplants the audio of one file onto the video of another, correcting all
desync. The algorithm itself lives in resync_core.py (the single source of
truth, SPEC.md); this file only does the platform-specific I/O: decoding the
videos to frame fingerprints, turning the Plan into an ffmpeg filter graph,
and muxing. Video is copied losslessly; only the audio is re-encoded.

Usage:
    python3 resync_audio.py AUDIO_SRC VIDEO_SRC OUTPUT [options]

    AUDIO_SRC : file whose audio you want to keep   (e.g. the Polish dub)
    VIDEO_SRC : file whose video you want to keep   (e.g. the HD BluRay)
    OUTPUT    : output file (.mp4 / .mkv)

Options:
    --pitch-correct    resample instead of time-stretch (undoes PAL pitch
                       rise; default keeps the donor audio's original pitch)
    --sub SEC          sub-segment length for wander tracking   (default 20)
    --abitrate RATE    output audio bitrate                     (default 256k)
    --dump-plan PATH   also write the Plan (SPEC.md section 5) as JSON
    --keep-temp        keep the intermediate fingerprint files

Requires: ffmpeg, ffprobe, numpy, resync_core.py alongside this file.
"""
import argparse, subprocess, sys, tempfile, os, json
import numpy as np
import resync_core
from resync_core import SIG


def run(cmd):
    """Run a command and capture its output (used for ffprobe / ffmpeg)."""
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd[:3])}...\n{p.stderr[-800:]}")
    return p.stdout


def probe(path):
    """Return (video_fps, duration_seconds, audio_sample_rate)."""
    info = json.loads(run(['ffprobe', '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', path]))
    v = next(s for s in info['streams'] if s['codec_type'] == 'video')
    a = next(s for s in info['streams'] if s['codec_type'] == 'audio')
    num, den = v['r_frame_rate'].split('/')
    return float(num) / float(den), float(info['format']['duration']), \
        int(a['sample_rate'])


def extract_sigs(path, raw_path):
    """SPEC.md Stage 1 (decode part): every frame -> SIG x SIG grayscale.
    Returns a raw (N, SIG*SIG) array; resync_core does the zero-meaning."""
    run(['ffmpeg', '-y', '-v', 'error', '-i', path,
         '-an', '-vf', f'scale={SIG}:{SIG},format=gray',
         '-f', 'rawvideo', raw_path])
    x = np.fromfile(raw_path, dtype=np.uint8)
    n = len(x) // (SIG * SIG)
    return x[:n * SIG * SIG].reshape(n, SIG * SIG)


def plan_to_filter(plan, tmp):
    """Executor (SPEC.md section 7): Plan -> ffmpeg filter_complex script.
    Reads donor audio as input [0:a], emits [aout]."""
    sr = plan['audio_sample_rate']
    pitch_correct = plan['pitch_mode'] == 'correct'
    lines, labels = [], []
    for n, item in enumerate(plan['items']):
        lab = f'p{n}'
        if item['kind'] == 'silence':
            lines.append(f"aevalsrc=0|0:d={item['duration']:.6f}:s={sr}[{lab}];")
        else:
            c0, c1 = item['donor_start'], item['donor_end']
            kdur, speed = item['target_duration'], item['speed']
            if pitch_correct:                # resample: also shifts pitch
                stretch = f'asetrate={sr}*{speed:.9f},aresample={sr}'
            else:                            # atempo: pitch-preserving
                stretch = f'atempo={speed:.9f}'
            lines.append(
                f"[0:a]atrim={c0:.6f}:{c1:.6f},asetpts=N/SR/TB,{stretch},"
                f"atrim=end={kdur:.6f},apad=whole_dur={kdur:.6f},"
                f"asetpts=N/SR/TB[{lab}];")
        labels.append(f'[{lab}]')
    lines.append(''.join(labels) + f'concat=n={len(labels)}:v=0:a=1[aout]')
    filt = os.path.join(tmp, 'filter.txt')
    with open(filt, 'w') as f:
        f.write('\n'.join(lines))
    return filt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('audio_src'); ap.add_argument('video_src')
    ap.add_argument('output')
    ap.add_argument('--pitch-correct', action='store_true')
    ap.add_argument('--sub', type=float, default=resync_core.SUB)
    ap.add_argument('--abitrate', default='256k')
    ap.add_argument('--dump-plan')
    ap.add_argument('--keep-temp', action='store_true')
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix='resync_')

    print('[1/4] probing files...')
    don_fps, don_audio_len, arate = probe(args.audio_src)
    tgt_fps, tgt_video_len, _ = probe(args.video_src)
    print(f'      donor : {don_fps:.4f} fps, {don_audio_len:.1f}s')
    print(f'      target: {tgt_fps:.4f} fps, {tgt_video_len:.1f}s')

    print('[2/4] extracting frame fingerprints (decodes both videos once)...')
    donor_fp = extract_sigs(args.audio_src, os.path.join(tmp, 'don.raw'))
    target_fp = extract_sigs(args.video_src, os.path.join(tmp, 'tgt.raw'))

    print('[3/4] building retiming plan (resync_core)...')
    plan = resync_core.build_plan(
        donor_fp, target_fp, don_fps, tgt_fps,
        don_audio_len, tgt_video_len, arate,
        pitch_mode='correct' if args.pitch_correct else 'preserve',
        sub=args.sub)
    nseg = sum(1 for i in plan['items'] if i['kind'] == 'segment')
    nsil = sum(1 for i in plan['items'] if i['kind'] == 'silence')
    print(f'      {nseg} sub-segments, {nsil} silence insert(s)')
    if args.dump_plan:
        with open(args.dump_plan, 'w') as f:
            json.dump(plan, f, indent=2)
        print(f'      plan written to {args.dump_plan}')

    print('[4/4] muxing output (video copied, audio re-encoded)...')
    filt = plan_to_filter(plan, tmp)
    run(['ffmpeg', '-y', '-v', 'warning',
         '-i', args.audio_src, '-i', args.video_src,
         '-filter_complex_script', filt,
         '-map', '1:v:0', '-map', '[aout]',
         '-c:v', 'copy', '-c:a', 'aac', '-b:a', args.abitrate,
         '-ar', str(arate), '-movflags', '+faststart', args.output])

    if not args.keep_temp:
        for f in ('don.raw', 'tgt.raw', 'filter.txt'):
            try: os.remove(os.path.join(tmp, f))
            except OSError: pass
        try: os.rmdir(tmp)
        except OSError: pass
    else:
        print(f'      temp files kept in {tmp}')
    print(f'\nDone -> {args.output}')


if __name__ == '__main__':
    main()
