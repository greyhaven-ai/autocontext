#!/usr/bin/env node
/**
 * Mirror the canonical JSON Schemas from
 *   ts/src/production-traces/contract/json-schemas/
 * into the Python package
 *   autocontext/src/autocontext/production_traces/contract/json_schemas/
 *
 * The Python runtime does not consume these files directly (Pydantic models in
 * models.py are the validation surface) but they ARE the authoritative schema
 * for ecosystem consumers and for debugging. Keeping a byte-identical copy
 * under the Python package gives third-party tools a Python-package path
 * without needing the TS repo checked out.
 *
 * Usage:
 *   node scripts/sync-python-production-traces-schemas.mjs          # mirror
 *   node scripts/sync-python-production-traces-schemas.mjs --check  # CI: diff-only
 *
 * A follow-up refactor (not in Layer 1) will regenerate models.py from these
 * schemas via datamodel-code-generator after we materialize a local-ref bundle.
 * For now, models.py is hand-maintained and cross-runtime compat is enforced
 * by the property tests (P5 in the spec).
 */
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TS_ROOT = resolve(__dirname, "..");
const PY_ROOT = resolve(TS_ROOT, "..", "autocontext");

const SRC_DIR = join(TS_ROOT, "src/production-traces/contract/json-schemas");
const DST_DIR = join(PY_ROOT, "src/autocontext/production_traces/contract/json_schemas");

const args = process.argv.slice(2);
const checkOnly = args.includes("--check");

if (!checkOnly) {
  mkdirSync(DST_DIR, { recursive: true });
}

const entries = readdirSync(SRC_DIR)
  .filter((f) => f.endsWith(".schema.json"))
  .sort();

let drift = false;
const actions = [];

for (const file of entries) {
  const src = join(SRC_DIR, file);
  const dst = join(DST_DIR, file);
  const srcBytes = readFileSync(src);
  let dstBytes;
  try {
    dstBytes = readFileSync(dst);
  } catch {
    dstBytes = null;
  }
  if (dstBytes === null || !srcBytes.equals(dstBytes)) {
    if (checkOnly) {
      drift = true;
      actions.push(`drift: ${file}`);
    } else {
      writeFileSync(dst, srcBytes);
      actions.push(`wrote: ${file}`);
    }
  }
}

// Also detect stale files in the destination that no longer exist in source.
let dstListing = [];
try {
  dstListing = readdirSync(DST_DIR).filter((f) => f.endsWith(".schema.json"));
} catch {
  // no-op — first run
}
const srcSet = new Set(entries);
for (const f of dstListing) {
  if (!srcSet.has(f)) {
    if (checkOnly) {
      drift = true;
      actions.push(`stale: ${f} should be deleted`);
    } else {
      // Defensive: don't delete outside DST_DIR.
      const p = join(DST_DIR, f);
      if (statSync(p).isFile()) {
        // Use unlinkSync indirectly via import — done inline.
        const { unlinkSync } = await import("node:fs");
        unlinkSync(p);
        actions.push(`deleted: ${f}`);
      }
    }
  }
}

if (checkOnly) {
  if (drift) {
    console.error("Python schema mirror has drift:");
    for (const a of actions) console.error("  " + a);
    console.error("Run: node scripts/sync-python-production-traces-schemas.mjs");
    process.exit(1);
  }
  console.log("Python schema mirror is up to date.");
  process.exit(0);
}

for (const a of actions) console.log(a);
if (actions.length === 0) console.log("Python schema mirror unchanged.");
