#!/usr/bin/env node
/**
 * Copy the generated cross-runtime role schemas into the npm package.
 *
 * Source development reads the canonical artifact from the repository's
 * docs/ directory. Published consumers do not have the repository around
 * them, so npm run build places the same artifact inside dist/, which is part
 * of the package's files allowlist.
 */

import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..");
const source = resolve(repoRoot, "docs", "role-output-schemas.json");
const distDir = resolve(here, "..", "dist");
const destination = resolve(distDir, "role-output-schemas.json");

if (!existsSync(source)) {
  console.error(`copy-role-output-schemas: source not found: ${source}`);
  process.exit(1);
}

mkdirSync(distDir, { recursive: true });
copyFileSync(source, destination);
console.log(`copy-role-output-schemas: ${source} -> ${destination}`);
