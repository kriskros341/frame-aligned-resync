import './style.css';
import { resync, type Progress } from './pipeline';

const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `
  <h1>frame-aligned-resync</h1>
  <p class="sub">Move a dub onto a higher-quality video by syncing through the
  picture &mdash; entirely in your browser, nothing is uploaded.</p>

  <label>
    <span>Donor &mdash; the file whose audio you want to keep</span>
    <input type="file" id="donor" accept="video/*,.mkv">
  </label>
  <label>
    <span>Target &mdash; the file whose video you want to keep</span>
    <input type="file" id="target" accept="video/*,.mkv">
  </label>

  <button id="run" disabled>Resync</button>
  <pre id="log">Pick a donor and a target file.</pre>
`;

const donorInput = document.querySelector<HTMLInputElement>('#donor')!;
const targetInput = document.querySelector<HTMLInputElement>('#target')!;
const runButton = document.querySelector<HTMLButtonElement>('#run')!;
const logEl = document.querySelector<HTMLPreElement>('#log')!;

function log(msg: string): void {
  logEl.textContent = msg;
}

function refreshRunState(): void {
  runButton.disabled = !(donorInput.files?.length && targetInput.files?.length);
}

donorInput.addEventListener('change', refreshRunState);
targetInput.addEventListener('change', refreshRunState);

const onProgress: Progress = (stage, fraction) => {
  const pct = Number.isNaN(fraction) ? '' : ` ${Math.round(fraction * 100)}%`;
  log(`${stage}${pct}…`);
};

runButton.addEventListener('click', async () => {
  const donor = donorInput.files?.[0];
  const target = targetInput.files?.[0];
  if (!donor || !target) return;

  runButton.disabled = true;
  try {
    const output = await resync(donor, target, onProgress);
    const url = URL.createObjectURL(output);
    log(`Done. Output ready: ${(output.size / 1048576).toFixed(1)} MiB`);
    // TODO: surface a download link for `url` in the UI.
    void url;
  } catch (err) {
    // Expected while the pipeline stages are still stubs.
    log(`Stopped: ${(err as Error).message}`);
  } finally {
    runButton.disabled = false;
  }
});
