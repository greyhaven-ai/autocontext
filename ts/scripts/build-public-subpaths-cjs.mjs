#!/usr/bin/env node
/**
 * Build the CommonJS bundles advertised by the integration and detector
 * subpath exports. The package is otherwise emitted as ESM by TypeScript, so
 * these focused bundles provide real `require()` entrypoints without changing
 * the primary module format.
 *
 * Production traces keeps its existing, separately budgeted CJS build.
 */
import { build } from "esbuild";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, "..");

const entryPoints = {
  "integrations/_shared/index": join(root, "src", "integrations", "_shared", "index.ts"),
  "integrations/anthropic/index": join(root, "src", "integrations", "anthropic", "index.ts"),
  "integrations/openai/index": join(root, "src", "integrations", "openai", "index.ts"),
  "control-plane/instrument/detectors/openai-python/index": join(
    root,
    "src",
    "control-plane",
    "instrument",
    "detectors",
    "openai-python",
    "index.ts",
  ),
  "control-plane/instrument/detectors/openai-ts/index": join(
    root,
    "src",
    "control-plane",
    "instrument",
    "detectors",
    "openai-ts",
    "index.ts",
  ),
  "control-plane/instrument/detectors/anthropic-python/index": join(
    root,
    "src",
    "control-plane",
    "instrument",
    "detectors",
    "anthropic-python",
    "index.ts",
  ),
  "control-plane/instrument/detectors/anthropic-ts/index": join(
    root,
    "src",
    "control-plane",
    "instrument",
    "detectors",
    "anthropic-ts",
    "index.ts",
  ),
};

await build({
  entryPoints,
  bundle: true,
  platform: "node",
  target: "node22",
  format: "cjs",
  outdir: join(root, "dist", "cjs"),
  outExtension: { ".js": ".cjs" },
  sourcemap: true,
  packages: "external",
  banner: {
    js: 'const AUTOCTX_CJS_IMPORT_META_URL = require("node:url").pathToFileURL(__filename).href;',
  },
  define: { "import.meta.url": "AUTOCTX_CJS_IMPORT_META_URL" },
  tsconfig: join(root, "tsconfig.json"),
  logLevel: "info",
});

console.log(
  `[build-public-subpaths-cjs] wrote ${Object.keys(entryPoints).length} bundles under ${join(root, "dist", "cjs")}`,
);
