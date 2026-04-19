// Retention-policy I/O helpers used by `init` and `prune`.
//
// Spec §6.6 defines the on-disk shape. Layer 8 is expected to own retention
// as a proper sub-module (with its own JSON schema and phase-2 ingest hook);
// Layer 7 ships the minimum that lets `init` drop a default file and `prune`
// act on it.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { canonicalJsonStringify } from "../../../control-plane/contract/canonical-json.js";
import { productionTracesRoot } from "../../ingest/paths.js";

const FILE_NAME = "retention-policy.json";

export type RetentionPolicy = {
  readonly schemaVersion: "1.0";
  readonly retentionDays: number;
  readonly preserveAll: boolean;
  readonly preserveCategories: readonly string[];
  readonly gcBatchSize: number;
};

export function retentionPolicyPath(cwd: string): string {
  return join(productionTracesRoot(cwd), FILE_NAME);
}

export function defaultRetentionPolicy(): RetentionPolicy {
  return {
    schemaVersion: "1.0",
    retentionDays: 90,
    preserveAll: false,
    preserveCategories: ["failure"],
    gcBatchSize: 1000,
  };
}

export async function loadRetentionPolicy(cwd: string): Promise<RetentionPolicy> {
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
  if (!isRetentionPolicy(parsed)) {
    throw new Error(
      `retention-policy.json: document does not match expected shape (schemaVersion, retentionDays, preserveAll, preserveCategories, gcBatchSize)`,
    );
  }
  return parsed;
}

export async function saveRetentionPolicy(
  cwd: string,
  policy: RetentionPolicy,
): Promise<void> {
  const path = retentionPolicyPath(cwd);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, canonicalJsonStringify(policy) + "\n", "utf-8");
}

function isRetentionPolicy(v: unknown): v is RetentionPolicy {
  if (v === null || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  if (r.schemaVersion !== "1.0") return false;
  if (typeof r.retentionDays !== "number" || r.retentionDays < 0) return false;
  if (typeof r.preserveAll !== "boolean") return false;
  if (!Array.isArray(r.preserveCategories)) return false;
  if (!r.preserveCategories.every((c) => typeof c === "string")) return false;
  if (typeof r.gcBatchSize !== "number" || r.gcBatchSize <= 0) return false;
  return true;
}
