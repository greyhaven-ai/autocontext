#!/usr/bin/env node
/**
 * AC-697 slice 5 packaging fix (PR #1000 review P2): the npm package
 * ships `docs/cli-contract.json` inside `dist/` so the
 * `capabilities` command can load it from a known location relative
 * to the running JS files. The previous design walked
 * `dist/cli/file.js` -> `..` three times to reach the repo root,
 * which works in the monorepo but lands outside the package in the
 * installed npm tarball.
 *
 * This script runs as part of `npm run build` (chained after `tsc`)
 * and copies the repo's contract source into `dist/cli-contract.json`.
 * The TS loader (`capabilities-command-workflow.ts`) resolves the
 * contract from there at runtime.
 */

import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
// `ts/scripts/copy-cli-contract.mjs` -> `ts/scripts` -> `ts` -> repo root.
const repoRoot = resolve(here, "..", "..");
const source = resolve(repoRoot, "docs", "cli-contract.json");
const distDir = resolve(here, "..", "dist");
const destination = resolve(distDir, "cli-contract.json");

if (!existsSync(source)) {
  console.error(`copy-cli-contract: source not found: ${source}`);
  process.exit(1);
}

mkdirSync(distDir, { recursive: true });
copyFileSync(source, destination);
console.log(`copy-cli-contract: ${source} -> ${destination}`);
