import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { describe, expect, it } from "vitest";

import {
  buildFailedInvestigationResult,
  deriveInvestigationName,
  normalizePositiveInteger,
  parseInvestigationJson,
  persistInvestigationArtifacts,
} from "../src/investigation/investigation-engine-helpers.js";

describe("investigation engine helpers", () => {
  it("derives stable names and parses wrapped JSON payloads", () => {
    expect(deriveInvestigationName("Why did checkout fail after Tuesday's deploy?")).toBe("why_did_checkout_fail");
    expect(parseInvestigationJson("before {\"a\":1} after")).toEqual({ a: 1 });
    expect(parseInvestigationJson("not json")).toBeNull();
  });

  it("normalizes positive integers", () => {
    expect(normalizePositiveInteger(3.9)).toBe(3);
    expect(normalizePositiveInteger(0)).toBeUndefined();
    expect(normalizePositiveInteger(undefined)).toBeUndefined();
  });

  it("persists artifacts and builds failed investigation results", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-investigation-helpers-"));
    try {
      const artifactDir = persistInvestigationArtifacts(
        dir,
        "checkout_rca",
        { diagnosis_target: "config regression" },
        "module.exports = { scenario: {} };",
      );

      expect(existsSync(join(artifactDir, "spec.json"))).toBe(true);
      expect(existsSync(join(artifactDir, "scenario.js"))).toBe(true);
      expect(existsSync(join(artifactDir, "scenario_type.txt"))).toBe(true);
      expect(readFileSync(join(artifactDir, "spec.json"), "utf-8")).toContain("checkout_rca");

      expect(buildFailedInvestigationResult(
        "inv-1",
        "checkout_rca",
        { description: "Investigate checkout regression" },
        ["spec invalid", "provider failed"],
      )).toMatchObject({
        id: "inv-1",
        name: "checkout_rca",
        status: "failed",
        error: "spec invalid; provider failed",
      });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
