// SPEC.md Stage 1 -- decode a video to 8x8 grayscale frame fingerprints,
// using WebCodecs' VideoDecoder fed by an mp4box.js demuxer.
//
// Instrumented with [resync] console logs while the browser glue is being
// debugged. MP4 inputs only -- mp4box does not demux MKV.

import { createFile, DataStream, MP4BoxBuffer } from 'mp4box';
import type { Fingerprints } from './types';
import type { Progress } from './pipeline';

const SIG = 8; // fingerprint is SIG x SIG, matching resync_core.SIG

type ISO = ReturnType<typeof createFile>;
interface Meta { fps: number; duration: number; audioRate: number; total: number }

/** Pure: a decoded frame -> 64 grayscale values (0..255), via an 8x8 draw. */
export function frameToFingerprint(
  frame: CanvasImageSource,
  ctx: OffscreenCanvasRenderingContext2D,
): Float32Array {
  ctx.drawImage(frame, 0, 0, SIG, SIG);
  const { data } = ctx.getImageData(0, 0, SIG, SIG); // RGBA
  const fp = new Float32Array(SIG * SIG);
  for (let i = 0; i < SIG * SIG; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    fp[i] = 0.299 * r + 0.587 * g + 0.114 * b; // Rec.601 luma
  }
  return fp;
}

/** Walk the sample-description box tree for the codec-config (avcC/hvcC/...). */
function codecDescription(file: ISO, trackId: number): Uint8Array | undefined {
  const trak = file.getTrackById(trackId) as any;
  const entries = trak?.mdia?.minf?.stbl?.stsd?.entries ?? [];
  for (const entry of entries) {
    const box = entry.avcC ?? entry.hvcC ?? entry.vpcC ?? entry.av1C;
    if (box) {
      const ds = new DataStream(undefined, 0); // default endianness: big
      box.write(ds);
      return new Uint8Array(ds.buffer, 8); // strip the 8-byte box header
    }
  }
  return undefined;
}

/**
 * SPEC.md Stage 1: decode every frame of `src` to an 8x8 fingerprint.
 * The whole file is appended at once -- robust for MP4s with the moov atom
 * at the end, where sample extraction must see the already-buffered mdat.
 */
export async function extractFingerprints(
  src: File,
  onProgress: Progress,
): Promise<Fingerprints> {
  const tag = `[resync] ${src.name}`;
  const file = createFile();
  const canvas = new OffscreenCanvas(SIG, SIG);
  // GPU-backed (no willReadFrequently): drawImage downscales each frame on
  // the GPU; only the tiny 8x8 result is read back.
  const ctx = canvas.getContext('2d')!;
  const frames: { t: number; fp: Float32Array }[] = [];
  const pending: EncodedVideoChunkInit[] = [];

  const state: { meta?: Meta } = {};
  let decodeError: string | null = null;
  let sampleCount = 0;
  let readyFired = false;

  const decoder = new VideoDecoder({
    output: (frame) => {
      frames.push({ t: frame.timestamp, fp: frameToFingerprint(frame, ctx) });
      frame.close();
      if (state.meta) onProgress('fingerprinting', frames.length / state.meta.total);
    },
    error: (e) => { decodeError ??= e.message; },
  });

  await new Promise<void>((resolve, reject) => {
    file.onError = (e: string) => reject(new Error(`mp4box: ${e}`));

    file.onReady = (info: any) => {
      readyFired = true;
      const tracks: any[] = info.tracks ?? [];
      console.log(`${tag}: onReady -- ${tracks.length} track(s)`, info);
      const v = tracks.find((t) => t.video);
      const a = tracks.find((t) => t.audio);
      if (!v) { reject(new Error(`no video track in ${src.name}`)); return; }

      const durationSec = v.duration / v.timescale;
      state.meta = {
        fps: v.nb_samples / durationSec,
        duration: durationSec,
        audioRate: a?.audio?.sample_rate ?? 0,
        total: v.nb_samples,
      };
      const description = codecDescription(file, v.id);
      console.log(`${tag}: video track`, {
        id: v.id, codec: v.codec, nb_samples: v.nb_samples,
        w: v.video?.width, h: v.video?.height,
        description: description ? `${description.length} bytes` : 'MISSING',
      });

      try {
        decoder.configure({
          codec: v.codec,
          codedWidth: v.video.width,
          codedHeight: v.video.height,
          description,
        });
      } catch (e) {
        reject(new Error(`decoder.configure failed for ${src.name}: ` +
          `${(e as Error).message}`));
        return;
      }

      // Collect sample descriptors; feed the decoder later with backpressure.
      file.onSamples = (_id: number, _user: unknown, samples: any[]) => {
        sampleCount += samples.length;
        for (const s of samples) {
          pending.push({
            type: s.is_sync ? 'key' : 'delta',
            timestamp: (s.cts / s.timescale) * 1e6,
            duration: (s.duration / s.timescale) * 1e6,
            data: s.data,
          });
        }
      };
      file.setExtractionOptions(v.id);
      file.start();
    };

    // One-shot append: read the whole file, hand it to mp4box, flush.
    src.arrayBuffer()
      .then((buf) => {
        console.log(`${tag}: feeding ${buf.byteLength} bytes`);
        file.appendBuffer(MP4BoxBuffer.fromArrayBuffer(buf, 0));
        file.flush();
        resolve();
      })
      .catch(reject);
  });

  console.log(`${tag}: onReady=${readyFired}, samples delivered=${sampleCount}`);
  if (!readyFired) {
    throw new Error(`mp4box never parsed ${src.name} (onReady did not fire) ` +
      `-- not a valid MP4?`);
  }
  if (!state.meta) throw new Error(`no usable video track in ${src.name}`);

  // Feed the decoder with backpressure: if too many decodes are queued, wait
  // for output to drain so VideoFrames don't pile up in GPU memory.
  for (let i = 0; i < pending.length; i++) {
    decoder.decode(new EncodedVideoChunk(pending[i]));
    if (decoder.decodeQueueSize > 64) {
      await new Promise<void>((r) => {
        const wait = () =>
          decoder.decodeQueueSize > 16 ? setTimeout(wait, 4) : r();
        wait();
      });
    }
  }
  console.log(`${tag}: fed ${pending.length} chunk(s) to the decoder`);

  try {
    await decoder.flush();
  } catch (e) {
    throw new Error(`video decode failed for ${src.name}: ${(e as Error).message}`);
  }
  decoder.close();
  console.log(`${tag}: decoded ${frames.length} frame(s)`);

  if (decodeError) {
    throw new Error(`video decoder error for ${src.name}: ${decodeError}`);
  }
  if (frames.length === 0) {
    throw new Error(`decoded 0 frames from ${src.name}: mp4box delivered ` +
      `${sampleCount} sample(s). See the [resync] console logs.`);
  }

  frames.sort((x, y) => x.t - y.t); // ensure presentation order
  const data = new Float32Array(frames.length * SIG * SIG);
  frames.forEach((f, i) => data.set(f.fp, i * SIG * SIG));

  // Sanity-log the fingerprint values: real frames have spatial structure
  // (a spread of pixel values); all-equal or all-zero means the decode/draw
  // produced garbage, which would make the cross-correlation find nothing.
  let mn = Infinity, mx = -Infinity, sum = 0;
  for (const v of data) { if (v < mn) mn = v; if (v > mx) mx = v; sum += v; }
  console.log(`${tag}: fingerprint pixels min=${mn.toFixed(1)} ` +
    `max=${mx.toFixed(1)} mean=${(sum / data.length).toFixed(1)} ` +
    `(expect a wide spread; min==max means a decode problem)`);

  return {
    data,
    frameCount: frames.length,
    fps: state.meta.fps,
    duration: state.meta.duration,
    audioSampleRate: state.meta.audioRate,
  };
}
