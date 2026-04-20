#!/usr/bin/env node
/**
 * Drift guard for A2-I instrument JSON Schemas.
 *
 * Validates that the schemas under
 *   ts/src/control-plane/instrument/contract/json-schemas/
 * are well-formed JSON Schema 2020-12, compile cleanly via AJV, and that the
 * hand-written TS types in `plugin-interface.ts` line up with the schemas via
 * the `_TypeCheck` assertion at the bottom of `validators.ts`.
 *
 * Usage:
 *   node scripts/check-instrument-schemas.mjs         # report
 *   node scripts/check-instrument-schemas.mjs --check # CI drift check (same thing here)
 *
 * This script is intentionally lightweight — the instrument schemas have no
 * Python consumer (A2-I is TS-only) and no codegen step, so no byte-diff check
 * is required. The TS type/schema alignment is enforced at compile time via
 * `validators.ts`'s `_TypeCheck` type union.
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

// Lazy-load ajv from node_modules — keeps this script self-contained.
const ajvMod = await import("ajv/dist/2020.js");
const AjvCtor = ajvMod.default.default ?? ajvMod.default;
const addFormatsMod = await import("ajv-formats");
const addFormatsFn = addFormatsMod.default.default ?? addFormatsMod.default;

const __dirname = dirname(fileURLToPath(import.meta.url));
const TS_ROOT = resolve(__dirname, "..");
const SCHEMAS_DIR = join(TS_ROOT, "src/control-plane/instrument/contract/json-schemas");

const EXPECTED_SCHEMAS = [
  "instrument-plan.schema.json",
  "instrument-session.schema.json",
];

const found = readdirSync(SCHEMAS_DIR).filter((f) => f.endsWith(".schema.json")).sort();

const expected = [...EXPECTED_SCHEMAS].sort();
if (JSON.stringify(found) !== JSON.stringify(expected)) {
  console.error(`instrument-schemas: drift — schema file set mismatch.`);
  console.error(`  expected: ${expected.join(", ")}`);
  console.error(`  found:    ${found.join(", ")}`);
  process.exit(1);
}

const ajv = new AjvCtor({ strict: true, allErrors: true });
addFormatsFn(ajv);

for (const f of found) {
  const raw = readFileSync(join(SCHEMAS_DIR, f), "utf-8");
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    console.error(`instrument-schemas: ${f} is not valid JSON: ${e.message}`);
    process.exit(1);
  }
  if (typeof parsed.$id !== "string" || !parsed.$id.startsWith("https://autocontext.dev/schema/")) {
    console.error(`instrument-schemas: ${f} is missing or has malformed $id`);
    process.exit(1);
  }
  try {
    ajv.addSchema(parsed);
  } catch (e) {
    console.error(`instrument-schemas: AJV failed to compile ${f}: ${e.message}`);
    process.exit(1);
  }
}

console.log(`instrument-schemas: ${found.length} schema(s) validated.`);
process.exit(0);
