import { describe, expect, it } from "vitest";

import {
  coerceSpecTypes,
  inferMissingFields,
} from "../src/scenarios/spec-auto-heal-core.js";

describe("spec auto-heal core workflow", () => {
  it("coerces nested numeric and boolean string fields", () => {
    const fixed = coerceSpecTypes({
      maxSteps: "10",
      retryable: "true",
      nested: { timeout: "30", enabled: "false" },
      steps: [{ qualityThreshold: "0.85" }],
    });

    expect(fixed).toEqual({
      maxSteps: 10,
      retryable: true,
      nested: { timeout: 30, enabled: false },
      steps: [{ qualityThreshold: 0.85 }],
    });
  });

  it("infers description and rubric without overwriting populated values", () => {
    const inferred = inferMissingFields({
      taskPrompt: "Analyze this code for bugs. Return the most likely defect.",
      description: "",
      rubric: "",
      judgeRubric: "",
    });
    const preserved = inferMissingFields({
      taskPrompt: "Test",
      description: "My description",
      rubric: "My rubric",
    });

    expect(inferred.description).toBeTruthy();
    expect(inferred.rubric || inferred.judgeRubric).toBeTruthy();
    expect(preserved).toMatchObject({
      description: "My description",
      rubric: "My rubric",
    });
  });
});
