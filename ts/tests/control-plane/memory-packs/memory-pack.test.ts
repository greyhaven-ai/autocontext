import { describe, expect, test } from "vitest";
import { validateOperationalMemoryPack } from "../../../src/control-plane/memory-packs/index.js";

describe("validateOperationalMemoryPack", () => {
  test("accepts sanitized reusable operational findings", () => {
    const result = validateOperationalMemoryPack({
      packId: "ops-contracts-v1",
      version: "1.0.0",
      createdAt: "2026-05-06T19:00:00.000Z",
      status: "sanitized",
      integrity: {
        status: "clean",
        notes: ["Derived from external eval diagnostics without task answers."],
      },
      findings: [
        {
          id: "preserve-sidecars",
          summary: "Preserve sidecar files before opening stateful stores.",
          evidenceRefs: ["runs/dev/db-repair/trace.jsonl#L10"],
          reusableBehavior: "Copy database sidecars before repair attempts.",
          targetFamilies: ["stateful-store", "terminal"],
          risk: "low",
        },
      ],
    });

    expect(result).toEqual({ valid: true });
  });

  test("rejects packs that mark answer leakage or secret leakage", () => {
    const result = validateOperationalMemoryPack({
      packId: "bad-pack",
      version: "1.0.0",
      createdAt: "2026-05-06T19:00:00.000Z",
      status: "sanitized",
      findings: [
        {
          id: "leaky",
          summary: "Contains a task answer.",
          evidenceRefs: ["trace"],
          reusableBehavior: "Do the exact answer.",
          targetFamilies: ["terminal"],
          risk: "high",
          containsTaskAnswer: true,
          containsSecret: true,
        },
      ],
    });

    expect(result).toMatchObject({ valid: false });
    if (!result.valid) {
      expect(result.errors).toContain("finding leaky contains task-specific answer material");
      expect(result.errors).toContain("finding leaky contains secret material");
    }
  });

  test("rejects malformed leakage flags instead of treating them as absent", () => {
    const result = validateOperationalMemoryPack({
      packId: "bad-pack",
      version: "1.0.0",
      createdAt: "2026-05-06T19:00:00.000Z",
      status: "sanitized",
      findings: [
        {
          id: "leaky",
          summary: "Contains malformed leakage flags.",
          evidenceRefs: ["trace"],
          reusableBehavior: "Use only sanitized behavior.",
          targetFamilies: ["terminal"],
          risk: "high",
          containsTaskAnswer: "true",
          containsSecret: "true",
        },
      ],
    });

    expect(result).toMatchObject({ valid: false });
    if (!result.valid) {
      expect(result.errors).toContain("finding leaky containsTaskAnswer must be a boolean when present");
      expect(result.errors).toContain("finding leaky containsSecret must be a boolean when present");
    }
  });
});
