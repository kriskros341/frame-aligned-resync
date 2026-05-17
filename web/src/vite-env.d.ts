/// <reference types="vite/client" />

// The Python algorithm core is imported as a raw string and handed to
// Pyodide at runtime. This keeps resync_core.py the single source of truth:
// the build embeds a snapshot of the real file, nothing is hand-copied.
declare module '*.py?raw' {
  const src: string;
  export default src;
}
