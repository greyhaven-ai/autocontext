import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { validateRedactionPolicy } from "../contract/validators.js";
import { canonicalJsonStringify } from "../../control-plane/contract/canonical-json.js";
import { productionTracesRoot } from "../ingest/paths.js";
import type { LoadedRedactionPolicy } from "./types.js";

/**
 * Load / save / default helpers for the per-installation redaction policy
 * stored at `.autocontext/production-traces/redaction-policy.json`. See spec
 * §7.1 for the on-disk shape.
 *
 * The loader validates the policy document via the AJV validator registered
 * in `contract/validators.ts` (single AJV instance per process). It refuses
 * to proceed if the document is malformed — the Layer 3 scan-workflow calls
 * this exactly once at init, so failing fast here surfaces config bugs
 * before any trace is ingested.
 */

const FILE_NAME = "redaction-policy.json";

export function redactionPolicyPath(cwd: string): string {
  return join(productionTracesRoot(cwd), FILE_NAME);
}

export function defaultRedactionPolicy(): LoadedRedactionPolicy {
  return {
    schemaVersion: "1.0",
    mode: "on-export",
    autoDetect: {
      enabled: true,
      categories: ["pii-email", "pii-phone", "pii-ssn", "pii-credit-card", "secret-token"],
    },
    customPatterns: [],
    rawProviderPayload: { behavior: "blanket-mark" },
    exportPolicy: {
      placeholder: "[redacted]",
      preserveLength: false,
      includeRawProviderPayload: false,
      includeMetadata: true,
      categoryOverrides: {},
    },
  };
}

/**
 * Read the redaction policy from disk. Returns defaults if the file is
 * missing. Throws with a descriptive message if the file is present but
 * fails schema validation.
 */
export async function loadRedactionPolicy(cwd: string): Promise<LoadedRedactionPolicy> {
  const path = redactionPolicyPath(cwd);
  if (!existsSync(path)) {
    return defaultRedactionPolicy();
  }
  const raw = await readFile(path, "utf-8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new Error(
      `redaction-policy.json: malformed JSON: ${stringifyError(err)}`,
    );
  }
  const result = validateRedactionPolicy(parsed);
  if (!result.valid) {
    throw new Error(
      `redaction-policy.json: validation failed: ${result.errors.join("; ")}`,
    );
  }
  // AJV has accepted the document against the policy schema; the runtime
  // shape is now guaranteed to match LoadedRedactionPolicy. The one cast
  // here bridges validator result -> branded TS type.
  return parsed as LoadedRedactionPolicy;
}

/**
 * Persist the redaction policy as canonical JSON (sorted keys) so repeated
 * writes of the same logical state produce byte-identical output.
 */
export async function saveRedactionPolicy(
  cwd: string,
  policy: LoadedRedactionPolicy,
): Promise<void> {
  // Validate before writing — catch drift at the call-site rather than on
  // the next load().
  const result = validateRedactionPolicy(policy);
  if (!result.valid) {
    throw new Error(
      `redaction-policy.json: cannot save invalid policy: ${result.errors.join("; ")}`,
    );
  }

  const path = redactionPolicyPath(cwd);
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, canonicalJsonStringify(policy) + "\n", "utf-8");
}

function stringifyError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
