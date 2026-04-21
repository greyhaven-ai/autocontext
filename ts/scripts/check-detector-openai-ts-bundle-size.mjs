/**
 * Bundle-size budget check for autoctx/detectors/openai-ts.
 * Budget: 15 KB gzipped. Run: node scripts/check-detector-openai-ts-bundle-size.mjs
 */
import { gzipSync } from "node:zlib";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const distFile = join(__dirname, "..", "dist", "control-plane", "instrument", "detectors", "openai-ts", "index.js");

if (!existsSync(distFile)) {
  console.log("SKIP — dist not built yet. Run `npm run build` first.");
  process.exit(0);
}

const raw = readFileSync(distFile);
const gz = gzipSync(raw);
const kb = (gz.length / 1024).toFixed(1);
const budget = 15;

if (gz.length / 1024 > budget) {
  console.error(`FAIL — detector-openai-ts: ${kb} KB gzipped exceeds budget of ${budget} KB.`);
  process.exit(1);
} else {
  console.log(`OK — detector-openai-ts: ${kb} KB gzipped (under ${budget} KB budget).`);
}
