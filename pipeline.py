#!/usr/bin/env python3
"""
pipeline.py  --  end-to-end: cda.pl download -> audio resync -> HEVC compress.

Runs the three stages in order, chaining the existing standalone scripts:
  1. download the Polish video+audio from a cda.pl URL  (built in, was cda_download.sh)
  2. transplant that Polish audio onto a target HD video (resync_audio.py)
  3. compress the result to HEVC                         (compress_result.py)

All intermediate files (the cda download and the re-synced video) are written
to a temporary directory which is deleted when the run finishes -- only the
final OUTPUT is left behind.

Usage:
    python3 pipeline.py CDA_URL VIDEO_SRC OUTPUT [options]

    CDA_URL    cda.pl video page URL (the one you copy from the browser/F12)
    VIDEO_SRC  the HD video whose picture you want to keep (e.g. the BluRay)
    OUTPUT     final compressed file (.mp4)

Options:
    --quality N         HEVC CQ/CRF, lower = better/larger   (default: 24)
    --cpu               compress on CPU (libx265) instead of GPU
    --preset P          encoder preset                       (default: slow)
    --pitch-correct     resample audio (undo PAL pitch shift) instead of
                        pitch-preserving time-stretch
    --skip-download     treat CDA_URL as an already-downloaded file path
    --keep-temp         keep the temp directory instead of deleting it

Requires: ffmpeg, ffprobe, curl, numpy, and resync_audio.py + compress_result.py
alongside this script.
"""
import argparse, subprocess, sys, os, re, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))

# browser headers cda.pl expects (ported from cda_download.sh)
HEADERS = {
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.7',
    'origin': 'https://www.cda.pl',
    'referer': 'https://www.cda.pl/',
    'sec-ch-ua': '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'sec-gpc': '1',
    'user-agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'),
}
HEADER_ARGS = []
for _k, _v in HEADERS.items():
    HEADER_ARGS += ['-H', f'{_k}: {_v}']


def run(cmd):
    """Run a command, streaming its output; abort on failure."""
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f'\npipeline aborted: command failed -> {cmd[0]} ...')


def find_baseurl(xml, content_type):
    """First <BaseURL> appearing after a contentType="..." marker."""
    i = xml.find(f'contentType="{content_type}"')
    if i < 0:
        sys.exit(f'could not find {content_type} stream in the cda.pl manifest')
    m = re.search(r'<BaseURL>\s*(.*?)\s*</BaseURL>', xml[i:], re.S)
    if not m:
        sys.exit(f'no BaseURL for {content_type} stream')
    return m.group(1).strip()


def download_cda(url, dest):
    """Stage 1: read the DASH manifest off the cda.pl page, download + mux.

    The page embeds the player config as a JSON `player_data` attribute; its
    `manifest_cast` field is the full .mpd URL (with escaped slashes), so the
    media host never has to be guessed.
    """
    page = subprocess.run(['curl', '-sL', *HEADER_ARGS, url],
                          capture_output=True, text=True).stdout
    m = re.search(r'"manifest_cast":"(https:[^"]+?\.mpd)"', page)
    if not m:
        sys.exit('could not find "manifest_cast" on the cda.pl page '
                 '-- check the URL (is the video public?)')
    mpd = m.group(1).replace('\\/', '/')          # unescape JSON slashes
    print(f'    manifest: {mpd}')

    xml = subprocess.run(['curl', '-s', *HEADER_ARGS, mpd],
                         capture_output=True, text=True).stdout
    if '<BaseURL>' not in xml:
        sys.exit('empty/invalid manifest -- check the URL')

    video_file = find_baseurl(xml, 'video')
    audio_file = find_baseurl(xml, 'audio')
    base = mpd.rsplit('/', 1)[0] + '/'            # media host + directory
    tmp_v, tmp_a = dest + '.v', dest + '.a'
    print(f'    video: {video_file}')
    print(f'    audio: {audio_file}')

    run(['curl', '-L', '-o', tmp_v, *HEADER_ARGS, base + video_file])
    run(['curl', '-L', '-o', tmp_a, *HEADER_ARGS, base + audio_file])
    run(['ffmpeg', '-y', '-v', 'error', '-i', tmp_v, '-i', tmp_a,
         '-c', 'copy', dest])
    for f in (tmp_v, tmp_a):
        try: os.remove(f)
        except OSError: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cda_url')
    ap.add_argument('video_src')
    ap.add_argument('output')
    ap.add_argument('--quality', type=int, default=24)
    ap.add_argument('--cpu', action='store_true')
    ap.add_argument('--preset', default='slow')
    ap.add_argument('--pitch-correct', action='store_true')
    ap.add_argument('--skip-download', action='store_true')
    ap.add_argument('--keep-temp', action='store_true')
    args = ap.parse_args()

    if not os.path.isfile(args.video_src):
        sys.exit(f'error: no such video file: {args.video_src}')

    tmp = tempfile.mkdtemp(prefix='cda_pipeline_')
    try:
        # ---- stage 1: download (into temp dir) --------------------------
        if args.skip_download:
            if not os.path.isfile(args.cda_url):
                sys.exit(f'error: --skip-download but no such file: {args.cda_url}')
            cda_file = args.cda_url
            print('[1/3] download : skipped (using existing file)')
        else:
            print('[1/3] download : fetching from cda.pl ...')
            cda_file = os.path.join(tmp, 'cda.mp4')
            download_cda(args.cda_url, cda_file)

        # ---- stage 2: resync (intermediate stays in temp dir) -----------
        print('\n[2/3] resync   : transplanting audio onto the HD video ...')
        synced = os.path.join(tmp, 'synced.mp4')
        cmd = ['python3', os.path.join(HERE, 'resync_audio.py'),
               cda_file, args.video_src, synced]
        if args.pitch_correct:
            cmd.append('--pitch-correct')
        run(cmd)

        # ---- stage 3: compress (final output) ---------------------------
        print('\n[3/3] compress : encoding to HEVC ...')
        cmd = ['python3', os.path.join(HERE, 'compress_result.py'),
               synced, args.output, '-q', str(args.quality),
               '--preset', args.preset]
        if args.cpu:
            cmd.append('--cpu')
        run(cmd)
    finally:
        if args.keep_temp:
            print(f'\ntemp directory kept: {tmp}')
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    size = os.path.getsize(args.output) / (1024 * 1024)
    print(f'\nPipeline complete -> {args.output}  ({size:.1f} MiB)')


if __name__ == '__main__':
    main()
