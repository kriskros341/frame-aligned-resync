import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    // Allow importing ../../resync_core.py — the shared algorithm core,
    // which lives in the repo root, one level above this Vite project.
    fs: { allow: ['..'] },
  },
  optimizeDeps: {
    // Pyodide loads its own .wasm/.data assets at runtime; pre-bundling it
    // with esbuild breaks that, so leave it out.
    exclude: ['pyodide'],
  },
  // Note: if threaded numpy is ever used inside Pyodide, the dev and preview
  // servers will need COOP/COEP headers set here.
});
