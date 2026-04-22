#!/usr/bin/env node
/**
 * Bundle-size budget check for `autoctx/integrations/anthropic`.
 *
 * Bundles the subpath entry via esbuild for a browser-ish target with
 * tree-shaking + minification, gzips with zlib default compression, and
 * asserts the result is ≤ BUDGET_BYTES (40 kB gzipped).
 *
 * Runs in CI on PRs touching `integrations/anthropic/**`, `package.json`, or
 * this script itself.
 *
 * Flags:
 *   --report    write `bundle-integrations-anthropic-report.txt` with sizes.
 *   --json      emit a JSON summary on stdout for tooling.
 *
 * Exit 1 on over-budget. Budget bumps are PR decisions.
 */
import { build } from "esbuild";
import { gzipSync } from "node:zlib";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

const BUDGET_BYTES = 40_960; // 40 kB gzipped ceiling (spec §6.1).

const ENTRY = join(ROOT, "src", "integrations", "anthropic", "index.ts");

const args = new Set(process.argv.slice(2));
const wantReport = args.has("--report");
const wantJson = args.has("--json");

const tmp = mkdtempSync(join(tmpdir(), "autoctx-integrations-anthropic-bundle-"));
const outFile = join(tmp, "bundle.js");

const NODE_BUILTINS = [
  "node:async_hooks",
  "node:crypto",
  "node:fs",
  "node:fs/promises",
  "node:path",
  "node:url",
  "node:os",
  "node:zlib",
  "node:child_process",
  "node:stream",
  "node:util",
];

let metafile;
try {
  const result = await build({
    entryPoints: [ENTRY],
    bundle: true,
    platform: "neutral",
    target: "es2022",
    format: "esm",
    minify: true,
    treeShaking: true,
    outfile: outFile,
    metafile: true,
    logLevel: "silent",
    // @anthropic-ai/sdk is a peer dep; ajv/ajv-formats/ulid are runtime deps
    // bundled into autoctx/production-traces and thus always present.
    external: [
      ...NODE_BUILTINS,
      "@anthropic-ai/sdk",
      "ajv",
      "ajv/dist/2020.js",
      "ajv-formats",
      "ulid",
    ],
    mainFields: ["module", "main"],
    conditions: ["import", "default"],
  });
  metafile = result.metafile;
} catch (err) {
  console.error("[bundle-size] esbuild failed:", err);
  process.exit(2);
}

const raw = readFileSync(outFile);
const gzipped = gzipSync(raw);
rmSync(tmp, { recursive: true, force: true });

const rawBytes = raw.byteLength;
const gzipBytes = gzipped.byteLength;
const headroom = BUDGET_BYTES - gzipBytes;
const overBudget = gzipBytes > BUDGET_BYTES;

if (wantJson) {
  process.stdout.write(
    JSON.stringify({ budgetBytes: BUDGET_BYTES, rawBytes, gzipBytes, headroom, overBudget }) + "\n",
  );
} else {
  console.log(`[bundle-size] autoctx/integrations/anthropic`);
  console.log(`[bundle-size] raw:      ${rawBytes.toLocaleString()} bytes`);
  console.log(`[bundle-size] gzipped:  ${gzipBytes.toLocaleString()} bytes`);
  console.log(`[bundle-size] budget:   ${BUDGET_BYTES.toLocaleString()} bytes`);
  console.log(`[bundle-size] headroom: ${headroom.toLocaleString()} bytes`);
}

if (wantReport) {
  const topModules = Object.entries(metafile.inputs)
    .map(([path, info]) => ({ path, bytes: info.bytes }))
    .sort((a, b) => b.bytes - a.bytes)
    .slice(0, 20);
  const lines = [
    `autoctx/integrations/anthropic bundle report`,
    `---------------------------------------------`,
    `raw:      ${rawBytes.toLocaleString()} bytes`,
    `gzipped:  ${gzipBytes.toLocaleString()} bytes`,
    `budget:   ${BUDGET_BYTES.toLocaleString()} bytes`,
    `headroom: ${headroom.toLocaleString()} bytes`,
    ``,
    `top module contributors (raw):`,
    ...topModules.map((m) => `  ${String(m.bytes).padStart(8)}  ${m.path}`),
    ``,
  ].join("\n");
  writeFileSync(
    join(ROOT, "bundle-integrations-anthropic-report.txt"),
    lines,
    "utf-8",
  );
  console.log(`[bundle-size] wrote bundle-integrations-anthropic-report.txt`);
}

if (overBudget) {
  console.error(
    `[bundle-size] FAIL — ${gzipBytes - BUDGET_BYTES} bytes over the ${BUDGET_BYTES}-byte budget.\n` +
      `  Re-run with --report to see the top contributors, or bump BUDGET_BYTES in\n` +
      `  scripts/check-integrations-anthropic-bundle-size.mjs if the addition is\n` +
      `  intentional and justified in the PR description.`,
  );
  process.exit(1);
}

if (!wantJson) console.log(`[bundle-size] OK — within budget.`);
