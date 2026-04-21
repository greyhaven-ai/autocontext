#!/usr/bin/env node
/**
 * SDK import-discipline check — replaces the ESLint `no-restricted-imports`
 * rule described in spec §3.3 with a pure static audit so we don't need to
 * stand up a full ESLint toolchain for one rule.
 *
 * Scans `src/production-traces/sdk/**\/*.ts` for imports. Fails CI if any
 * import targets a path outside the allowlist:
 *   - `production-traces/contract/**`
 *   - `production-traces/redaction/install-salt.ts` or `hash-primitives.ts`
 *   - `control-plane/contract/canonical-json.ts`
 *   - Node built-ins (`node:*`)
 *   - Direct runtime deps (`ajv`, `ajv-formats`, `ulid`)
 *
 * Enterprise anchor: prevents the SDK's tree-shakability contract from
 * silently regressing when someone adds a convenient but fat import.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const SDK_DIR = join(ROOT, "src", "production-traces", "sdk");

const ALLOWED_RELATIVE_PREFIXES = [
  "../contract/",
  "./", // intra-sdk is allowed
  "../redaction/install-salt",
  "../redaction/hash-primitives",
  "../../control-plane/contract/canonical-json",
  "../../control-plane/contract/branded-ids",
];

const ALLOWED_BARE_IMPORTS = new Set([
  "ajv",
  "ajv/dist/2020.js",
  "ajv-formats",
  "ulid",
]);

function isAllowedImport(spec) {
  if (spec.startsWith("node:")) return true;
  if (ALLOWED_BARE_IMPORTS.has(spec)) return true;
  if (spec.startsWith(".")) {
    return ALLOWED_RELATIVE_PREFIXES.some((p) => spec.startsWith(p));
  }
  return false;
}

function listTsFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) out.push(...listTsFiles(full));
    else if (entry.endsWith(".ts")) out.push(full);
  }
  return out;
}

const IMPORT_RE = /^\s*(?:import|export)\s+(?:[^'"]*\s+from\s+)?["']([^"']+)["']/gm;

let failed = false;
for (const file of listTsFiles(SDK_DIR)) {
  const body = readFileSync(file, "utf-8");
  for (const match of body.matchAll(IMPORT_RE)) {
    const spec = match[1];
    if (!isAllowedImport(spec)) {
      console.error(
        `[check-sdk-import-discipline] FAIL ${relative(ROOT, file)}: disallowed import "${spec}"`,
      );
      failed = true;
    }
  }
}

if (failed) {
  console.error("\nSDK import-discipline check FAILED. See spec §3.3 for the allowlist.");
  process.exit(1);
}
console.log(`[check-sdk-import-discipline] OK — ${listTsFiles(SDK_DIR).length} files pass.`);
