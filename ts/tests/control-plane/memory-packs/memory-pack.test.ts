import { describe, expect, test } from "vitest";
import {
  compileOperationalMemoryContext,
  validateOperationalMemoryPack,
} from "../../../src/control-plane/memory-packs/index.js";

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

describe("compileOperationalMemoryContext", () => {
  test("selects bounded family-matched findings and records skipped findings", () => {
    const context = compileOperationalMemoryContext({
      contextId: "tb-dev10-selected-v1",
      createdAt: "2026-05-11T15:00:00.000Z",
      taskId: "hf-model-inference",
      targetFamilies: ["terminal", "artifact-contract"],
      maxFindings: 1,
      riskTolerance: "medium",
      packs: [
        {
          packId: "tb-dev10-memory",
          version: "1.0.0",
          createdAt: "2026-05-11T14:00:00.000Z",
          status: "sanitized",
          integrity: { status: "clean" },
          findings: [
            {
              id: "required-artifact-contract",
              summary: "Verify required output artifacts at checked paths.",
              evidenceRefs: ["runs/dev10/hf-model-inference/tests.log"],
              reusableBehavior: "Read every required artifact from its checked path before finishing.",
              targetFamilies: ["terminal", "artifact-contract"],
              risk: "low",
              containsTaskAnswer: false,
              containsSecret: false,
            },
            {
              id: "schema-key-contract",
              summary: "Validate exact output schema keys.",
              evidenceRefs: ["runs/dev10/structured-output/tests.log"],
              reusableBehavior: "Read structured output back and compare required key names.",
              targetFamilies: ["terminal", "structured-output"],
              risk: "low",
              containsTaskAnswer: false,
              containsSecret: false,
            },
            {
              id: "domain-correctness-validation",
              summary: "Validate numeric quality.",
              evidenceRefs: ["runs/dev10/raman-fitting/tests.log"],
              reusableBehavior: "Add an independent numeric reasonableness check.",
              targetFamilies: ["numeric-analysis"],
              risk: "medium",
              containsTaskAnswer: false,
              containsSecret: false,
            },
            {
              id: "high-risk-terminal",
              summary: "Use only when explicitly requested.",
              evidenceRefs: ["runs/dev10/high-risk/tests.log"],
              reusableBehavior: "Apply a broad terminal workflow rewrite.",
              targetFamilies: ["terminal"],
              risk: "high",
              containsTaskAnswer: false,
              containsSecret: false,
            },
            {
              id: "leaky-finding",
              summary: "Leaky finding.",
              evidenceRefs: ["runs/dev10/leaky/tests.log"],
              reusableBehavior: "Contains secret material.",
              targetFamilies: ["terminal"],
              risk: "low",
              containsTaskAnswer: false,
              containsSecret: true,
            },
            {
              id: "required-artifact-contract",
              summary: "Duplicate artifact guidance.",
              evidenceRefs: ["runs/dev10/duplicate/tests.log"],
              reusableBehavior: "Duplicate guidance should not be repeated.",
              targetFamilies: ["terminal", "artifact-contract"],
              risk: "low",
              containsTaskAnswer: false,
              containsSecret: false,
            },
          ],
        },
      ],
    });

    expect(context.selectedFindings.map((finding) => finding.findingId)).toEqual([
      "required-artifact-contract",
    ]);
    expect(context.selectedFindings[0]).toMatchObject({
      packId: "tb-dev10-memory",
      matchedTargetFamilies: ["terminal", "artifact-contract"],
    });
    expect(context.prompt).toContain("Read every required artifact from its checked path");
    expect(context.prompt).not.toContain("Read structured output back");
    expect(context.skippedFindings.map((finding) => [finding.findingId, finding.reason])).toEqual([
      ["domain-correctness-validation", "target-family-mismatch"],
      ["high-risk-terminal", "risk-too-high"],
      ["leaky-finding", "leakage-risk"],
      ["required-artifact-contract", "duplicate-finding"],
      ["schema-key-contract", "capacity-limit"],
    ]);
  });

  test("quarantines findings from non-clean memory packs", () => {
    const context = compileOperationalMemoryContext({
      contextId: "contaminated-context",
      createdAt: "2026-05-11T15:05:00.000Z",
      targetFamilies: ["terminal"],
      packs: [
        {
          packId: "contaminated-pack",
          version: "1.0.0",
          createdAt: "2026-05-11T14:00:00.000Z",
          status: "sanitized",
          integrity: { status: "contaminated", notes: ["read held-out answer"] },
          findings: [
            {
              id: "do-not-apply",
              summary: "Should not be applied.",
              evidenceRefs: ["runs/heldout/trace.jsonl"],
              reusableBehavior: "This pack is contaminated.",
              targetFamilies: ["terminal"],
              risk: "low",
              containsTaskAnswer: false,
              containsSecret: false,
            },
          ],
        },
      ],
    });

    expect(context.selectedFindings).toEqual([]);
    expect(context.skippedFindings).toEqual([
      {
        packId: "contaminated-pack",
        findingId: "do-not-apply",
        reason: "pack-integrity-not-clean",
        detail: "integrity=contaminated",
      },
    ]);
    expect(context.prompt).toBe("");
  });
});
