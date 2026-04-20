#!/usr/bin/env node
/**
 * Regenerate ts/src/production-traces/contract/generated-types.ts from the
 * canonical JSON Schemas under ts/src/production-traces/contract/json-schemas/.
 *
 * Usage:
 *   node scripts/generate-production-traces-types.mjs           # write file
 *   node scripts/generate-production-traces-types.mjs --check   # diff-only (CI)
 *
 * In --check mode, exits non-zero if the regenerated output differs from the
 * committed file, without modifying anything.
 */
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { compile } from "json-schema-to-typescript";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TS_ROOT = resolve(__dirname, "..");
const SCHEMAS_DIR = join(TS_ROOT, "src/production-traces/contract/json-schemas");
const OUTPUT_FILE = join(TS_ROOT, "src/production-traces/contract/generated-types.ts");

const BANNER = [
  "/* eslint-disable */",
  "// AUTO-GENERATED from src/production-traces/contract/json-schemas/ — DO NOT EDIT.",
  "// Regenerate with: node scripts/generate-production-traces-types.mjs",
  "// CI gate: node scripts/generate-production-traces-types.mjs --check",
].join("\n");

// Load every *.schema.json. We compile each with a fresh $refOptions.resolve
// entry that lets json-schema-ref-parser resolve cross-file $refs by $id.
const files = readdirSync(SCHEMAS_DIR)
  .filter((f) => f.endsWith(".schema.json"))
  .sort();

const schemasById = new Map();
for (const f of files) {
  const full = join(SCHEMAS_DIR, f);
  const raw = JSON.parse(readFileSync(full, "utf-8"));
  if (typeof raw.$id !== "string") {
    throw new Error(`${f}: schema missing top-level $id`);
  }
  schemasById.set(raw.$id, raw);
}

// Custom resolver plugin: maps known $id URLs to the in-memory schema.
const idResolver = {
  order: 1,
  canRead: true,
  async read(file) {
    // `file.url` is the $id url json-schema-ref-parser was given.
    const s = schemasById.get(file.url);
    if (!s) throw new Error(`idResolver: unknown $id ${file.url}`);
    return JSON.stringify(s);
  },
};

const options = {
  bannerComment: "",
  additionalProperties: false,
  declareExternallyReferenced: true,
  unknownAny: true,
  style: { singleQuote: false, semi: true, printWidth: 120 },
  $refOptions: {
    resolve: {
      autocontext: idResolver,
    },
  },
  cwd: SCHEMAS_DIR,
};

// Compile order: shared-defs first (pulls in its $defs), then documents.
// But because declareExternallyReferenced=true and each doc compiles
// independently, we just emit each doc's output and de-duplicate types at the
// end by dropping any repeated declaration by name.
const outputs = [];
for (const f of files) {
  const full = join(SCHEMAS_DIR, f);
  const schema = JSON.parse(readFileSync(full, "utf-8"));
  // json-schema-to-typescript wants a `name` when compiling a schema object.
  // Use the schema's title if present, else the filename base.
  const name = schema.title ?? f.replace(/\.schema\.json$/, "");
  // eslint-disable-next-line no-await-in-loop
  const ts = await compile(schema, name, options);
  outputs.push(`// ---- ${f} ----\n${ts.trim()}\n`);
}

// Merge the per-schema outputs. We accept duplicate interface declarations
// because TypeScript requires unique names; dedupe by keeping the first
// declaration of each top-level `export (interface|type|enum) Name`.
const merged = dedupeDeclarations(outputs.join("\n"));

const final = `${BANNER}\n\n${merged.trim()}\n`;

const args = process.argv.slice(2);
if (args.includes("--check")) {
  let existing = "";
  try {
    existing = readFileSync(OUTPUT_FILE, "utf-8");
  } catch (e) {
    console.error(`check: cannot read ${OUTPUT_FILE}: ${e.message}`);
    process.exit(1);
  }
  if (existing !== final) {
    console.error("drift detected: generated-types.ts differs from canonical schemas.");
    console.error("run: node scripts/generate-production-traces-types.mjs");
    process.exit(1);
  }
  console.log("generated-types.ts is up to date.");
  process.exit(0);
}

writeFileSync(OUTPUT_FILE, final);
console.log(`wrote ${OUTPUT_FILE}`);

function dedupeDeclarations(src) {
  const seen = new Set();
  const lines = src.split("\n");
  const out = [];
  let skipping = false;
  let braceDepth = 0;
  for (const line of lines) {
    if (skipping) {
      // Track brace depth to know when we exit a skipped declaration.
      for (const ch of line) {
        if (ch === "{") braceDepth += 1;
        else if (ch === "}") braceDepth -= 1;
      }
      if (braceDepth <= 0) {
        skipping = false;
        braceDepth = 0;
      }
      continue;
    }
    const m = line.match(/^export (interface|type|enum)\s+([A-Za-z0-9_]+)/);
    if (m) {
      const name = m[2];
      if (seen.has(name)) {
        skipping = true;
        for (const ch of line) {
          if (ch === "{") braceDepth += 1;
          else if (ch === "}") braceDepth -= 1;
        }
        // Type aliases (export type Foo = ...;) often terminate on the same line.
        if (m[1] === "type" && line.trim().endsWith(";")) {
          skipping = false;
          braceDepth = 0;
        }
        continue;
      }
      seen.add(name);
    }
    out.push(line);
  }
  return out.join("\n");
}
