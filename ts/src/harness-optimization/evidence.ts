import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { validateCandidateEvidence } from "./contract/validators.js";
import type { CandidateEvidence } from "./contract/generated-types.js";

// Re-export the validation surface so callers have a single import site.
export { validateCandidateEvidence };
export type { CandidateEvidence };

/**
 * Load and validate a CandidateEvidence artifact from a JSON file.
 * Throws if the file content violates the schema.
 */
export function readCandidateEvidence(path: string): CandidateEvidence {
  const raw = JSON.parse(readFileSync(path, "utf8")) as unknown;
  const result = validateCandidateEvidence(raw);
  if (!result.valid) {
    throw new Error(`invalid CandidateEvidence at ${path}: ${result.errors.join("; ")}`);
  }
  return raw as CandidateEvidence;
}

/**
 * Persist a CandidateEvidence artifact as JSON with stable 2-space indentation
 * and a trailing newline. Parent directories are created as needed.
 */
export function writeCandidateEvidence(evidence: CandidateEvidence, path: string): void {
  const result = validateCandidateEvidence(evidence);
  if (!result.valid) {
    throw new Error(`invalid CandidateEvidence for ${path}: ${result.errors.join("; ")}`);
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(evidence, null, 2)}\n`);
}
