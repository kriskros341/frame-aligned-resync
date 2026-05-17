// SPEC.md §7 -- realize a Plan into a finished file.
//
// NOT fully browser-verified. assemblePcm (audio.ts) is pure and tested;
// the WebCodecs + mp4box + mp4-muxer glue here is best-effort. Instrumented
// with [resync] console logs. MP4 inputs only.

import { createFile, DataStream, MP4BoxBuffer } from 'mp4box';
import { Muxer, ArrayBufferTarget } from 'mp4-muxer';
import type { Plan } from './types';
import { assemblePcm } from './audio';

type ISO = ReturnType<typeof createFile>;

/** MPEG-4 AAC sampling-frequency index table. */
const AAC_FREQ = [
  96000, 88200, 64000, 48000, 44100, 32000,
  24000, 22050, 16000, 12000, 11025, 8000, 7350,
];

/**
 * Build the 2-byte AudioSpecificConfig for AAC-LC -- the `description` an
 * AudioDecoder needs. (Deriving it is far more robust than digging the esds
 * box tree out of mp4box.)
 */
function aacConfig(sampleRate: number, channels: number): Uint8Array {
  const freqIdx = AAC_FREQ.indexOf(sampleRate);
  if (freqIdx < 0) throw new Error(`unsupported AAC sample rate ${sampleRate}`);
  const objectType = 2; // AAC-LC
  const bits = (objectType << 11) | (freqIdx << 7) | (channels << 3);
  return new Uint8Array([(bits >> 8) & 0xff, bits & 0xff]);
}

/** Stream a File into an mp4box demuxer (one-shot append). */
async function feed(file: ISO, src: File): Promise<void> {
  const buf = await src.arrayBuffer();
  file.appendBuffer(MP4BoxBuffer.fromArrayBuffer(buf, 0));
  file.flush();
}

/** avcC / hvcC box bytes for a video track (mp4-muxer's decoderConfig). */
function videoDescription(file: ISO, trackId: number): Uint8Array | undefined {
  const trak = file.getTrackById(trackId) as any;
  for (const entry of trak?.mdia?.minf?.stbl?.stsd?.entries ?? []) {
    const box = entry.avcC ?? entry.hvcC;
    if (box) {
      const ds = new DataStream(undefined, 0);
      box.write(ds);
      return new Uint8Array(ds.buffer, 8); // strip the 8-byte box header
    }
  }
  return undefined;
}

interface DecodedAudio {
  channels: Float32Array[];
  sampleRate: number;
}

/** Decode the donor's audio track to planar Float32 PCM. */
async function decodeAudio(src: File): Promise<DecodedAudio> {
  const file = createFile();
  const pending: EncodedAudioChunkInit[] = [];
  let track: any = null;

  await new Promise<void>((resolve, reject) => {
    file.onError = (e: string) => reject(new Error(`mp4box: ${e}`));
    file.onReady = (info: any) => {
      track = (info.tracks ?? []).find((t: any) => t.audio);
      if (!track) { reject(new Error('no audio track in donor file')); return; }
      file.onSamples = (_i: number, _u: unknown, samples: any[]) => {
        for (const s of samples) pending.push({
          type: s.is_sync ? 'key' : 'delta',
          timestamp: (s.cts / s.timescale) * 1e6,
          duration: (s.duration / s.timescale) * 1e6,
          data: s.data,
        });
      };
      file.setExtractionOptions(track.id);
      file.start();
    };
    feed(file, src).then(resolve).catch(reject);
  });

  const sampleRate: number = track.audio.sample_rate;
  const numberOfChannels: number = track.audio.channel_count;
  console.log(`[resync] donor audio: codec ${track.codec}, ${sampleRate} Hz, ` +
    `${numberOfChannels}ch, ${pending.length} packets`);

  const parts: Float32Array[][] = [];
  let decodeErr: string | null = null;
  const decoder = new AudioDecoder({
    output: (ad) => {
      for (let c = 0; c < ad.numberOfChannels; c++) {
        const buf = new Float32Array(ad.numberOfFrames);
        ad.copyTo(buf, { planeIndex: c, format: 'f32-planar' });
        (parts[c] ??= []).push(buf);
      }
      ad.close();
    },
    error: (e) => { decodeErr ??= e.message; },
  });

  const cfg: AudioDecoderConfig = {
    codec: track.codec, sampleRate, numberOfChannels,
    description: aacConfig(sampleRate, numberOfChannels),
  };
  const support = await AudioDecoder.isConfigSupported(cfg);
  if (!support.supported) {
    throw new Error(`audio decoder rejected the donor config ` +
      `(codec ${track.codec}, ${sampleRate} Hz, ${numberOfChannels}ch)`);
  }
  decoder.configure(cfg);

  for (let i = 0; i < pending.length; i++) {
    decoder.decode(new EncodedAudioChunk(pending[i]));
    if (decoder.decodeQueueSize > 128) {
      await new Promise<void>((r) => {
        const w = () => (decoder.decodeQueueSize > 32 ? setTimeout(w, 4) : r());
        w();
      });
    }
  }
  try {
    await decoder.flush();
  } catch (e) {
    throw new Error(`audio decode failed: ${(e as Error).message}`);
  }
  decoder.close();
  if (decodeErr) throw new Error(`audio decoder error: ${decodeErr}`);

  const concat = (chunks: Float32Array[]) => {
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const out = new Float32Array(total);
    let o = 0;
    for (const c of chunks) { out.set(c, o); o += c.length; }
    return out;
  };
  return { channels: parts.map(concat), sampleRate };
}

interface VideoChunks {
  chunks: EncodedVideoChunk[];
  codec: string;
  width: number;
  height: number;
  description?: Uint8Array;
}

/** Demux the target's video track into encoded chunks, to copy verbatim. */
async function demuxVideo(src: File): Promise<VideoChunks> {
  const file = createFile();
  const chunks: EncodedVideoChunk[] = [];
  let info!: Omit<VideoChunks, 'chunks'>;

  await new Promise<void>((resolve, reject) => {
    file.onError = (e: string) => reject(new Error(`mp4box: ${e}`));
    file.onReady = (movie: any) => {
      const v = (movie.tracks ?? []).find((t: any) => t.video);
      if (!v) { reject(new Error('no video track in target file')); return; }
      info = {
        codec: v.codec, width: v.video.width, height: v.video.height,
        description: videoDescription(file, v.id),
      };
      file.onSamples = (_i: number, _u: unknown, samples: any[]) => {
        for (const s of samples) chunks.push(new EncodedVideoChunk({
          type: s.is_sync ? 'key' : 'delta',
          timestamp: (s.cts / s.timescale) * 1e6,
          duration: (s.duration / s.timescale) * 1e6,
          data: s.data,
        }));
      };
      file.setExtractionOptions(v.id);
      file.start();
    };
    feed(file, src).then(resolve).catch(reject);
  });
  console.log(`[resync] target video: codec ${info.codec}, ` +
    `${info.width}x${info.height}, ${chunks.length} chunks`);
  return { chunks, ...info };
}

/** Encode planar PCM to AAC chunks for the muxer. */
async function encodeAudio(
  pcm: Float32Array[],
  sampleRate: number,
): Promise<{ chunk: EncodedAudioChunk; meta?: EncodedAudioChunkMetadata }[]> {
  const out: { chunk: EncodedAudioChunk; meta?: EncodedAudioChunkMetadata }[] = [];
  const channels = pcm.length;
  const cfg: AudioEncoderConfig = {
    codec: 'mp4a.40.2', sampleRate, numberOfChannels: channels, bitrate: 256_000,
  };
  const support = await AudioEncoder.isConfigSupported(cfg);
  if (!support.supported) {
    throw new Error(`this browser cannot encode AAC ` +
      `(${sampleRate} Hz, ${channels}ch) -- AudioEncoder config unsupported`);
  }
  const encoder = new AudioEncoder({
    output: (chunk, meta) => out.push({ chunk, meta }),
    error: (e) => { throw new Error(`audio encode error: ${e.message}`); },
  });
  encoder.configure(cfg);

  const BLOCK = 1024;
  for (let off = 0; off < pcm[0].length; off += BLOCK) {
    const n = Math.min(BLOCK, pcm[0].length - off);
    const planar = new Float32Array(n * channels);
    for (let c = 0; c < channels; c++) {
      planar.set(pcm[c].subarray(off, off + n), c * n);
    }
    encoder.encode(new AudioData({
      format: 'f32-planar', sampleRate, numberOfFrames: n,
      numberOfChannels: channels, timestamp: (off / sampleRate) * 1e6, data: planar,
    }));
  }
  await encoder.flush();
  encoder.close();
  return out;
}

const muxerCodec = (c: string): 'avc' | 'hevc' =>
  c.startsWith('hev') || c.startsWith('hvc') ? 'hevc' : 'avc';

/**
 * SPEC.md §7: Plan + the two source files -> a finished MP4 Blob.
 * Video is copied (not re-encoded); only the audio is rebuilt and encoded.
 */
export async function executePlan(
  plan: Plan,
  donor: File,
  target: File,
): Promise<Blob> {
  const audio = await decodeAudio(donor);
  const pcm = assemblePcm(plan, audio.channels, audio.sampleRate);
  console.log(`[resync] assembled ${pcm[0]?.length ?? 0} samples/channel`);
  const aac = await encodeAudio(pcm, audio.sampleRate);
  const video = await demuxVideo(target);

  const muxer = new Muxer({
    target: new ArrayBufferTarget(),
    fastStart: 'in-memory',
    video: { codec: muxerCodec(video.codec), width: video.width, height: video.height },
    audio: { codec: 'aac', numberOfChannels: pcm.length, sampleRate: audio.sampleRate },
  });
  video.chunks.forEach((c, i) => {
    // mp4-muxer only needs the decoderConfig on the first chunk.
    muxer.addVideoChunk(c, i === 0 && video.description ? {
      decoderConfig: {
        codec: video.codec, codedWidth: video.width,
        codedHeight: video.height, description: video.description,
      },
    } : undefined);
  });
  for (const { chunk, meta } of aac) muxer.addAudioChunk(chunk, meta);
  muxer.finalize();
  console.log('[resync] mux complete');

  return new Blob([(muxer.target as ArrayBufferTarget).buffer], { type: 'video/mp4' });
}
