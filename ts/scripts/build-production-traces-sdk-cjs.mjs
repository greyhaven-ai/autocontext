#!/usr/bin/env node
/**
 * Build the CJS flavor of the `autoctx/production-traces` subpath export.
 *
 * Why this exists: the main package is ESM-only (tsconfig `"module": "ESNext"`
 * + JSON imports with `with { type: "json" }` + `import.meta.url`), which
 * the `tsc` CommonJS emitter cannot produce directly. esbuild handles all
 * three seamlessly, so we use it solely for the customer-facing SDK subpath.
 *
 * Output: `dist/cjs/production-traces/sdk/index.cjs` — a single bundled
 * CommonJS file that can be required via
 *
 *     const { buildTrace } = require("autoctx/production-traces");
 *
 * Bundles all contract / redaction / canonical-json dependencies into the
 * one file. The ESM entry at `dist/production-traces/sdk/index.js` (from
 * `tsc`) retains tree-shakability; CJS customers on Node 18+ get a
 * functional `require()` without the native-ESM-from-CJS gymnastics.
 *
 * Enterprise-discipline anchors:
 *   - No network access (esbuild is a local binary).
 *   - No telemetry emission.
 *   - Deterministic output for the same source commit.
 */
import { build } from "esbuild";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdirSync } from "node:fs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const outFile = join(root, "dist", "cjs", "production-traces", "sdk", "index.cjs");
mkdirSync(dirname(outFile), { recursive: true });

await build({
  entryPoints: [join(root, "src", "production-traces", "sdk", "index.ts")],
  bundle: true,
  platform: "node",
  target: "node18",
  format: "cjs",
  outfile: outFile,
  sourcemap: true,
  logLevel: "info",
  // Keep runtime deps as external so customers' lockfiles resolve them from
  // their own node_modules — the bundle is for our own code only.
  external: ["ajv", "ajv/dist/2020.js", "ajv-formats", "ulid"],
  tsconfig: join(root, "tsconfig.json"),
});

console.log(`[build-production-traces-sdk-cjs] wrote ${outFile}`);
