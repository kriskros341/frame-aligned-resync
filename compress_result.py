#!/usr/bin/env python3
"""
compress_result.py  --  shrink a finished video to HEVC / H.265.

Re-encodes ONLY the video. Audio is copied untouched, so the resynced
Polish track is never degraded a second time.

Usage:
    python3 compress_result.py INPUT [OUTPUT] [options]

    INPUT     video to compress
    OUTPUT    output file            (default: INPUT_compressed.mp4)

Options:
    --cpu        use libx265 on the CPU (slow) instead of the GPU
    -q QUALITY   CQ/CRF value, lower = better & larger
                 sensible range 18-28          (default: 24)
    --preset P   encoder preset                 (default: slow)

By default it uses the Nvidia GPU encoder (hevc_nvenc) -- fast, and
known to work on this machine. Pass --cpu only if the GPU is unavailable.

Requires: ffmpeg.
"""
import argparse, subprocess, sys, os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input')
    ap.add_argument('output', nargs='?')
    ap.add_argument('--cpu', action='store_true', help='encode on CPU (libx265)')
    ap.add_argument('-q', '--quality', type=int, default=24)
    ap.add_argument('--preset', default='slow')
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f'error: no such file: {args.input}')
    out = args.output or f'{os.path.splitext(args.input)[0]}_compressed.mp4'

    if args.cpu:
        print(f'encoder : libx265 (CPU)   crf={args.quality}   (slow)')
        vopts = ['-c:v', 'libx265', '-crf', str(args.quality),
                 '-preset', args.preset]
    else:
        print(f'encoder : hevc_nvenc (GPU)   cq={args.quality}')
        vopts = ['-c:v', 'hevc_nvenc', '-cq', str(args.quality),
                 '-preset', args.preset]

    before = os.path.getsize(args.input)
    print(f'input   : {args.input}')
    print(f'output  : {out}\n')

    # -tag:v hvc1 keeps the HEVC mp4 playable in QuickTime / Apple players
    cmd = ['ffmpeg', '-y', '-i', args.input,
           *vopts, '-tag:v', 'hvc1',
           '-c:a', 'copy',
           '-movflags', '+faststart', out]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        hint = '' if args.cpu else '  (GPU encode failed -- retry with --cpu)'
        sys.exit(f'\nffmpeg failed (exit {r.returncode}){hint}')

    after = os.path.getsize(out)
    mib = 1024 * 1024
    print(f'\nbefore  : {before/mib:8.1f} MiB')
    print(f'after   : {after/mib:8.1f} MiB')
    print(f'saved   : {(1 - after/before)*100:8.1f} %')


if __name__ == '__main__':
    main()
