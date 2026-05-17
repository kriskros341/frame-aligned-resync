# frame-aligned-resync — web

Browser port of `frame-aligned-resync`. Runs **fully client-side**: the user's
two video files never leave their machine, so the whole thing is a static site
on a CDN with no server-side processing.

## Architecture

| Concern              | Tech                      | Where |
|----------------------|---------------------------|-------|
| App shell / UI       | TypeScript + Vite         | `src/main.ts` |
| Video decode (Stage 1) | WebCodecs `VideoDecoder` (hardware) | `src/pipeline.ts` |
| Algorithm (Stages 2–9) | the **Python core**, run via Pyodide | `src/pipeline.ts` → Pyodide |
| Audio stretch + mux  | WASM time-stretch + JS muxer | `src/pipeline.ts` |

The algorithm is **not** reimplemented here. The Python core is the single
source of truth (see `../SPEC.md`); Pyodide runs that exact code in the
browser. Only the I/O — decoding and muxing — is browser-specific, and that
part is allowed to differ per platform.

The `Plan` type in `src/types.ts` mirrors `SPEC.md §5`; it is the contract
between the core and the executor.

## Status

All four pipeline stages are implemented. What's been verified differs by
stage — see below.

| Stage | File | Verified? |
|-------|------|-----------|
| `extractFingerprints` (Stage 1) | `src/decode.ts` | ❌ browser-only, not yet run |
| `buildPlan` (Stages 2–9)        | `src/core.ts` + `resync_core.py` | ✅ `npm run verify` |
| audio assembly (part of §7)     | `src/audio.ts` | ✅ `npm run verify:audio` |
| `executePlan` glue (§7)         | `src/execute.ts` | ❌ browser-only, not yet run |

`decode.ts` and `execute.ts` are WebCodecs + mp4box + mp4-muxer glue: they
typecheck and bundle, but cannot be exercised outside a browser, so they
will need a browser session to debug. Likely trouble spots: the AAC
`description` extraction, and the mp4-muxer video-chunk copy calls.

**Limitation:** mp4box demuxes **MP4 only** — an `.mkv` source must be
remuxed to MP4 first (`ffmpeg -i in.mkv -c copy out.mp4`).

## Develop

```bash
npm install
npm run dev
```

## Build

```bash
npm run build      # tsc + vite build -> dist/
```

## Verify

```bash
npm run verify        # Pyodide core path (boots Pyodide, runs resync_core.py)
npm run verify:audio  # pure audio assembly (src/audio.ts)
```

## TODO

- [x] `buildPlan` — Pyodide runs `resync_core.py`, verified
- [x] audio assembly — `assemblePcm`, verified
- [x] `extractFingerprints` / `executePlan` — implemented (browser-untested)
- [ ] Debug `decode.ts` / `execute.ts` in a real browser
- [ ] MKV demux (mp4box handles MP4 only)
- [ ] Download link for the finished file in the UI
- [ ] Optional: pitch-preserving mode (needs a WASM time-stretcher)
