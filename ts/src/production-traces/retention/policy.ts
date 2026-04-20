// Retention-policy I/O — canonical home (spec §6.6).
//
// This module is the extraction of the retention-policy load/save helpers that
// shipped provisionally at `cli/_shared/retention-policy.ts` in Layer 7. The
// on-disk shape is unchanged; the only behavioural difference is that parsing
// now goes through the AJV-backed `validateRetentionPolicy` derived from the
// canonical JSON Schema (`contract/json-schemas/retention-policy.schema.json`),
// so the type-guard and the schema can no longer drift.
//
// Vocabulary (verbatim from spec §6.6):
//   - retentionDays
//   - preserveAll
//   - preserveCategories
//   - gcBatchSize

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";
import { validateRetentionPolicy } from "../contract/validators.js";
import { productionTracesRoot } from "../ingest/paths.js";

const FILE_NAME = "retention-policy.json";

/**
 * Retention policy as persisted to disk. Shape per spec §6.6; validated via
 * AJV against `contract/json-schemas/retention-policy.schema.json`.
 */
export type RetentionPolicy = {
  readonly schemaVersion: "1.0";
  readonly retentionDays: number;
  readonly preserveAll: boolean;
  readonly preserveCategories: readonly string[];
  readonly gcBatchSize: number;
};

/**
 * Loaded retention policy — identical shape to the on-disk type for v1, but
 * kept as a distinct nominal type so we can attach loaded-only invariants
 * later without a schema bump.
 */
export type LoadedRetentionPolicy = RetentionPolicy;

/** Absolute path of the on-disk retention-policy file. */
export function retentionPolicyPath(cwd: string): string {
  return join(productionTracesRoot(cwd), FILE_NAME);
}

/** Spec §6.6 defaults: 90-day retention, preserve failures, 1k-per-run cap. */
export function defaultRetentionPolicy(): RetentionPolicy {
  return {
    schemaVersion: "1.0",
    retentionDays: 90,
    preserveAll: false,
    preserveCategories: ["failure"],
    gcBatchSize: 1000,
  };
}

/**
 * Load the retention policy from disk, falling back to defaults when the file
 * is absent. Malformed JSON or schema-invalid documents throw — operators must
 * fix the file before `ingest` or `prune` will proceed.
 */
export async function loadRetentionPolicy(cwd: string): Promise<LoadedRetentionPolicy> {
  const path = retentionPolicyPath(cwd);
  if (!existsSync(path)) return defaultRetentionPolicy();
  const raw = await readFile(path, "utf-8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(
      `retention-policy.json: malformed JSON: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  const result = validateRetentionPolicy(parsed);
  if (!result.valid) {
    throw new Error(
      `retention-policy.json: schema validation failed: ${result.errors.join("; ")}`,
    );
  }
  return parsed as LoadedRetentionPolicy;
}

/**
 * Save the retention policy to disk via canonical JSON so the file is
 * byte-deterministic across hosts (matches the redaction-policy convention).
 */
export async function saveRetentionPolicy(
  cwd: string,
  policy: RetentionPolicy,
): Promise<void> {
  const path = retentionPolicyPath(cwd);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, canonicalJsonStringify(policy) + "\n", "utf-8");
}
