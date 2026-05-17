# frame-aligned-resync

Tools for putting a Polish dub (downloaded from [cda.pl](https://www.cda.pl)) onto a high-definition video (e.g. a BluRay), keeping the two perfectly
in sync.

## Motivation

My friend tried to find Avatar: The legend of Korra dubbed in Polish for free. She "borrowed" BluRay with original voice lines, but she prefers dubbing. We found an upload with horrendous video quality on CDA. It was like camera recorded screen TV or something.

## The problem this solves

A cda.pl low quality video with polish dubbing and HD BluRay of the same episode are *not* directly interchangeable:

- They run at slightly different speeds. The cda copy is usually decimated from 25 fps mastering, dropping frames in bursts, so the offset doesn't simply drift linearly.

- The broadcast version often has hard cuts: a few seconds trimmed from scenes, shorter intro/credits, etc.

First I tried automatically picking the right constant delay or speed up, but it was futile. the desync has *speed + wander + discrete cuts* all at once, which makes this really challanging

## How the fix works

The two files contain the same animation, just different qualities of it. Visuals therefore provide a reliable reference, even though audio languages differ.

1. Extract a tiny per-frame brightness 8x8 fingerprint from each video.
2. Cross-correlate the fingerprints to build an exact `cda_time → hd_time` map.
3. From the map, derive the speed ratio, track the wander, and locate the cuts.
4. Retime the Polish audio to the HD timeline in ~20 s sub-segments (pitch-preserving), inserting silence where the dub simply has no footage.
5. Mux-combine the retimed audio with the untouched HD video.

## Requirements

- `ffmpeg` + `ffprobe`
- `curl`
- Python 3 with `numpy`
- Optionally an Nvidia GPU for fast HEVC encoding (`hevc_nvenc`)

## Scripts

### `cda_download.sh`
Downloads a video from a cda.pl page URL (parses the DASH manifest, fetches the video and audio streams, combines them with ffmpeg).

```bash
./cda_download.sh "https://www.cda.pl/video/XXXXXXXXX"
# -> gotowy_film.mp4
```

### `resync_audio.py`
Transplants the audio of one file onto the video of another, correcting all desync. Video is copied losslessly; only the audio is re-encoded.

```bash
python3 resync_audio.py AUDIO_SRC VIDEO_SRC OUTPUT [options]

  AUDIO_SRC          file whose audio to keep   (the Polish dub)
  VIDEO_SRC          file whose video to keep   (the HD rip)
  OUTPUT             output file (.mp4 / .mkv)

  --pitch-correct    resample instead of time-stretch (undoes the PAL pitch
                     rise; default keeps the dub's original/familiar pitch)
  --sub SEC          sub-segment length for wander tracking   (default 20)
  --abitrate RATE    output audio bitrate                     (default 256k)
  --keep-temp        keep the intermediate fingerprint files
```

Example:

```bash
python3 resync_audio.py gotowy_film.mp4 \
    The.Legend.Of.Korra.S01E01.1080p.BluRay.x264-DeBTViD.mkv \
    korra_S01E01_PL.mp4
```

### `compress_result.py`
Compresses a finished video to HEVC/H.265. Re-encodes only the video. the audio is copied untouched.

```bash
python3 compress_result.py INPUT [OUTPUT] [options]

  --cpu              encode on CPU (libx265) instead of the GPU
  -q, --quality N    CQ/CRF value, lower = better/larger   (default: 24)
  --preset P         encoder preset                        (default: slow)
```

Defaults to the GPU encoder (`hevc_nvenc`). If a GPU encode fails, retry with
`--cpu`.

### `pipeline.py`
Runs the whole chain in one command: cda.pl download → resync → HEVC compress.
It chains the three scripts above; all intermediate files (the cda download and
the re-synced video) live in a temporary directory that is deleted when the run
finishes — only the final `OUTPUT` is left behind.

```bash
python3 pipeline.py CDA_URL VIDEO_SRC OUTPUT [options]

  --quality N        HEVC CQ/CRF, lower = better/larger   (default: 24)
  --cpu              compress on CPU (libx265) instead of GPU
  --preset P         encoder preset                       (default: slow)
  --pitch-correct    resample audio instead of pitch-preserving stretch
  --skip-download    treat CDA_URL as an already-downloaded file path
  --keep-temp        keep the temp directory instead of deleting it
```

Example:

```bash
python3 pipeline.py "https://www.cda.pl/video/XXXXXXXXX" \
    episode_1080p.mkv  episode_PL.mp4
```

Note on disk use: during the run the temp directory transiently holds the
re-synced video (~1 GB, uncompressed). It is removed once compression finishes,
but that space must be free *while* the pipeline runs.

## Typical workflow

```bash
# 1. download the Polish dub
./cda_download.sh "https://www.cda.pl/video/XXXXXXXXX"

# 2. resync it onto the HD video
python3 resync_audio.py gotowy_film.mp4 episode_1080p.mkv episode_PL.mp4

# 3. compress
python3 compress_result.py episode_PL.mp4 episode_PL_small.mp4
```

## Caveats

- Where the Polish broadcast genuinely lacks footage, the output is silent. This is intentional because there is literally no audio to place there.
- Assumes the donor audio fits within the target's timeline (donor shorter or similar length). The mirror case (donor longer) is not handled.
