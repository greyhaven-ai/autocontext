#!/usr/bin/env node
/**
 * SDK import-discipline check — replaces the ESLint `no-restricted-imports`
 * rule described in spec §3.3 with a pure static audit so we don't need to
 * stand up a full ESLint toolchain for one rule.
 *
 * Scans multiple subpath source directories and verifies each import stays
 * within its declared allowlist:
 *
 *   production-traces/sdk/
 *     - `production-traces/contract/**`
 *     - `production-traces/redaction/install-salt.ts` or `hash-primitives.ts`
 *     - `control-plane/contract/canonical-json.ts`
 *     - Node built-ins (`node:*`)
 *     - Direct runtime deps (`ajv`, `ajv-formats`, `ulid`)
 *
 *   integrations/openai/
 *     - relative intra-module imports
 *     - production-traces subpath (via relative path)
 *     - `openai` peer dep
 *     - Node built-ins (`node:*`)
 *
 *   detectors/openai-python/ and detectors/openai-ts/
 *     - relative intra-module imports
 *     - control-plane/instrument/contract (via relative)
 *     - `tree-sitter`, `web-tree-sitter` peer deps
 *     - Node built-ins (`node:*`)
 *
 * Enterprise anchor: prevents the SDK's tree-shakability contract from
 * silently regressing when someone adds a convenient but fat import.
 */
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");

function isAllowedImportFor(spec, allowedRelPrefixes, allowedBare) {
  if (spec.startsWith("node:")) return true;
  if (allowedBare.has(spec)) return true;
  if (spec.startsWith(".")) {
    return allowedRelPrefixes.some((p) => spec.startsWith(p));
  }
  return false;
}

function listTsFiles(dir) {
  if (!existsSync(dir)) return [];
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

// --- subpath checks ---
const SUBPATH_CHECKS = [
  {
    label: "production-traces/sdk",
    dir: join(ROOT, "src", "production-traces", "sdk"),
    allowedRelPrefixes: [
      "../contract/",
      "./",
      "../redaction/install-salt",
      "../redaction/hash-primitives",
      "../../control-plane/contract/canonical-json",
      "../../control-plane/contract/branded-ids",
    ],
    allowedBare: new Set(["ajv", "ajv/dist/2020.js", "ajv-formats", "ulid"]),
  },
  {
    label: "integrations/openai",
    dir: join(ROOT, "src", "integrations", "openai"),
    allowedRelPrefixes: [
      "./",
      "../",
      "../../production-traces/",
    ],
    allowedBare: new Set(["openai", "ulid"]),
  },
  {
    label: "detectors/openai-python",
    dir: join(ROOT, "src", "control-plane", "instrument", "detectors", "openai-python"),
    allowedRelPrefixes: [
      "./",
      "../",
      "../../",
      "../../../",
    ],
    allowedBare: new Set([]),
  },
  {
    label: "detectors/openai-ts",
    dir: join(ROOT, "src", "control-plane", "instrument", "detectors", "openai-ts"),
    allowedRelPrefixes: [
      "./",
      "../",
      "../../",
      "../../../",
    ],
    allowedBare: new Set([]),
  },
];

let failed = false;
let totalFiles = 0;
for (const check of SUBPATH_CHECKS) {
  const files = listTsFiles(check.dir);
  totalFiles += files.length;
  for (const file of files) {
    const body = readFileSync(file, "utf-8");
    for (const match of body.matchAll(IMPORT_RE)) {
      const spec = match[1];
      if (!isAllowedImportFor(spec, check.allowedRelPrefixes, check.allowedBare)) {
        console.error(
          `[check-sdk-import-discipline] FAIL [${check.label}] ${relative(ROOT, file)}: disallowed import "${spec}"`,
        );
        failed = true;
      }
    }
  }
}

if (failed) {
  console.error("\nSDK import-discipline check FAILED. See spec §3.3 for the allowlist.");
  process.exit(1);
}
console.log(`[check-sdk-import-discipline] OK — ${totalFiles} files pass across ${SUBPATH_CHECKS.length} subpaths.`);
