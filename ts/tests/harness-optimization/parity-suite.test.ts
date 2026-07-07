import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import {
  validateCandidateEvidence,
  validatePromotionScore,
  validateRepairResult,
  validateIntegrityMetadata,
  validateFrontierMechanism,
  validateOrphanMechanism,
  validateCalibrationReport,
} from "../../src/harness-optimization/contract/validators.js";

const REPO = join(import.meta.dirname, "../../..");
const FIX = join(REPO, "fixtures", "harness-optimization");
const SCHEMA_DIR = join(REPO, "ts", "src", "harness-optimization", "contract", "json-schemas");
const manifest = JSON.parse(readFileSync(join(FIX, "parity-manifest.json"), "utf8"));

const VALIDATORS: Record<string, (x: unknown) => { valid: boolean }> = {
  "candidate-evidence": validateCandidateEvidence,
  "promotion-score": validatePromotionScore,
  "repair-result": validateRepairResult,
  "integrity-metadata": validateIntegrityMetadata,
  "frontier-mechanism": validateFrontierMechanism,
  "orphan-mechanism": validateOrphanMechanism,
  "calibration-report": validateCalibrationReport,
};

const load = (rel: string) => JSON.parse(readFileSync(join(REPO, "fixtures", rel), "utf8"));

describe("harness-optimization parity suite", () => {
  it("manifest covers every schema", () => {
    const schemaFiles = readdirSync(SCHEMA_DIR)
      .filter((f) => f.endsWith(".schema.json") && f !== "_aggregate.schema.json")
      .sort();
    const manifestFiles = manifest.artifacts
      .map((a: { schema_file: string }) => a.schema_file)
      .sort();
    expect(manifestFiles).toEqual(schemaFiles);
  });

  for (const a of manifest.artifacts) {
    it(`${a.name}: clean fixtures validate, invalid rejected`, () => {
      expect(a.valid.length).toBeGreaterThan(0);
      expect(a.invalid.length).toBeGreaterThan(0);
      const validate = VALIDATORS[a.name];
      for (const rel of a.valid) expect(validate(load(rel))).toEqual({ valid: true });
      for (const rel of a.invalid) expect(validate(load(rel)).valid).toBe(false);
    });
  }
});
