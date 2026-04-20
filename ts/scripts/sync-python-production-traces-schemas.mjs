#!/usr/bin/env node
/**
 * Sync canonical JSON Schemas and regenerate Pydantic models for the Python
 * production-traces package.
 *
 * Pipeline:
 *   1. Mirror schemas: ts/.../json-schemas/ → autocontext/.../json_schemas/
 *   2. Bundle $refs in the aggregate `production-trace.schema.json` using a
 *      custom local URL resolver (maps `https://autocontext.dev/schema/
 *      production-traces/*.json` to the local canonical files).
 *   3. Feed the bundled aggregate to `datamodel-codegen` to regenerate
 *      `autocontext/.../contract/models.py`.
 *   4. Rewrite the generator's default banner to our AUTO-GENERATED banner.
 *
 * The Pydantic side only needs `ProductionTrace` (the top-level aggregate).
 * `redaction-policy.schema.json` is mirrored as a schema file for ecosystem
 * consumers but is not currently consumed by Pydantic — redaction-policy
 * loading is TS-only in v1.
 *
 * Usage:
 *   node scripts/sync-python-production-traces-schemas.mjs          # regenerate
 *   node scripts/sync-python-production-traces-schemas.mjs --check  # CI: drift-check only
 *
 * Drift check compares the would-be-generated `models.py` byte-for-byte with
 * the committed file. Any difference exits non-zero.
 */
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import $RefParser from "@apidevtools/json-schema-ref-parser";

const __dirname = dirname(fileURLToPath(import.meta.url));
const TS_ROOT = resolve(__dirname, "..");
const PY_ROOT = resolve(TS_ROOT, "..", "autocontext");

const SRC_DIR = join(TS_ROOT, "src/production-traces/contract/json-schemas");
const DST_SCHEMAS_DIR = join(PY_ROOT, "src/autocontext/production_traces/contract/json_schemas");
const MODELS_PY_PATH = join(PY_ROOT, "src/autocontext/production_traces/contract/models.py");

const URL_PREFIX = "https://autocontext.dev/schema/production-traces/";
const AGGREGATE_FOR_PYDANTIC = "production-trace.schema.json";

const args = process.argv.slice(2);
const checkOnly = args.includes("--check");

const actions = [];
let drift = false;

// -- Step 1: mirror the schemas directory ------------------------------------

if (!checkOnly) {
  mkdirSync(DST_SCHEMAS_DIR, { recursive: true });
}

const srcSchemas = readdirSync(SRC_DIR).filter((f) => f.endsWith(".schema.json")).sort();

for (const file of srcSchemas) {
  const src = join(SRC_DIR, file);
  const dst = join(DST_SCHEMAS_DIR, file);
  const srcBytes = readFileSync(src);
  let dstBytes = null;
  try {
    dstBytes = readFileSync(dst);
  } catch {
    // doesn't exist yet
  }
  if (dstBytes === null || !srcBytes.equals(dstBytes)) {
    if (checkOnly) {
      drift = true;
      actions.push(`drift: schema ${file}`);
    } else {
      writeFileSync(dst, srcBytes);
      actions.push(`wrote schema: ${file}`);
    }
  }
}

// Detect stale schemas (present in destination, absent from source).
let dstSchemaListing = [];
try {
  dstSchemaListing = readdirSync(DST_SCHEMAS_DIR).filter((f) => f.endsWith(".schema.json"));
} catch {
  // first run
}
const srcSet = new Set(srcSchemas);
for (const f of dstSchemaListing) {
  if (!srcSet.has(f)) {
    const p = join(DST_SCHEMAS_DIR, f);
    if (checkOnly) {
      drift = true;
      actions.push(`stale schema: ${f} should be deleted`);
    } else if (statSync(p).isFile()) {
      unlinkSync(p);
      actions.push(`deleted schema: ${f}`);
    }
  }
}

// -- Step 2: bundle $refs in the aggregate ------------------------------------

const localResolver = {
  order: 1,
  canRead: (f) => f.url.startsWith(URL_PREFIX),
  read: (f) => {
    const filename = f.url.slice(URL_PREFIX.length).replace(".json", ".schema.json");
    return readFileSync(join(SRC_DIR, filename), "utf-8");
  },
};

const aggregateEntry = JSON.parse(readFileSync(join(SRC_DIR, AGGREGATE_FOR_PYDANTIC), "utf-8"));
const bundled = await $RefParser.bundle(aggregateEntry, {
  resolve: { local: localResolver, http: false },
});

// -- Step 3: invoke datamodel-codegen on the bundled schema -------------------

const tmpDir = mkdtempSync(join(tmpdir(), "autoctx-pydantic-gen-"));
const bundledPath = join(tmpDir, "production-trace.bundled.json");
const generatedPath = join(tmpDir, "models.py");
writeFileSync(bundledPath, JSON.stringify(bundled, null, 2));

let generatedBody;
try {
  execFileSync(
    "datamodel-codegen",
    [
      "--input", bundledPath,
      "--input-file-type", "jsonschema",
      "--output", generatedPath,
      "--output-model-type", "pydantic_v2.BaseModel",
      "--use-annotated",
      "--use-title-as-name",
      "--enum-field-as-literal", "all",
      "--field-constraints",
      "--disable-timestamp",
      "--target-python-version", "3.11",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  generatedBody = readFileSync(generatedPath, "utf-8");
} finally {
  rmSync(tmpDir, { recursive: true, force: true });
}

// -- Step 4: rewrite header banner to our convention -------------------------

const OUR_BANNER = `# AUTO-GENERATED from ts/src/production-traces/contract/json-schemas/ — DO NOT EDIT.
# Run: node ts/scripts/sync-python-production-traces-schemas.mjs
# CI gate: node ts/scripts/sync-python-production-traces-schemas.mjs --check
`;

// datamodel-codegen emits a two-line banner starting with "# generated by ..."
// Replace those two lines with ours. The rest of the file (imports + classes)
// is preserved verbatim so the diff-for-drift check is meaningful.
const withBanner = generatedBody.replace(
  /^# generated by datamodel-codegen:\n#\s+filename:\s+\S+\n/,
  OUR_BANNER,
);

if (withBanner === generatedBody) {
  // Banner replacement didn't match — generator output format changed.
  console.error("sync:schemas: datamodel-codegen output banner did not match expected shape.");
  console.error("sync:schemas: please inspect generator output and update the banner regex.");
  process.exit(2);
}

// -- Compare / write models.py ------------------------------------------------

let existingBody = null;
try {
  existingBody = readFileSync(MODELS_PY_PATH, "utf-8");
} catch {
  // first run / doesn't exist yet
}

if (existingBody !== withBanner) {
  if (checkOnly) {
    drift = true;
    actions.push("drift: models.py regeneration produces different output");
  } else {
    writeFileSync(MODELS_PY_PATH, withBanner);
    actions.push("wrote models.py");
  }
}

// -- Report & exit ------------------------------------------------------------

if (checkOnly) {
  if (drift) {
    console.error("Python production-traces sync has drift:");
    for (const a of actions) console.error("  " + a);
    console.error("Run: node scripts/sync-python-production-traces-schemas.mjs");
    process.exit(1);
  }
  console.log("Python production-traces schemas + models are up to date.");
  process.exit(0);
}

for (const a of actions) console.log(a);
if (actions.length === 0) console.log("Python production-traces sync unchanged.");
