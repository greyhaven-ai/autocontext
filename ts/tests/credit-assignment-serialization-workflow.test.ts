import { describe, expect, it } from "vitest";

import {
  buildZeroCredits,
  computeTotalChangeMagnitude,
  normalizeAttributionResultData,
  normalizeComponentChangeData,
  normalizeCreditAssignmentRecordData,
  normalizeGenerationChangeVectorData,
} from "../src/analytics/credit-assignment-serialization-workflow.js";
import {
  AttributionResult,
  ComponentChange,
  CreditAssignmentRecord,
  GenerationChangeVector,
} from "../src/analytics/credit-assignment.js";

describe("credit assignment serialization workflow", () => {
  it("normalizes dict payloads and computes stable magnitudes/zero credits", () => {
    expect(normalizeComponentChangeData({ component: "playbook", magnitude: "0.4", description: 7 })).toEqual({
      component: "playbook",
      magnitude: 0.4,
      description: "7",
      metadata: {},
    });

    expect(
      normalizeGenerationChangeVectorData({
        generation: "2",
        score_delta: "0.3",
        changes: [{ component: "playbook" }],
      }),
    ).toEqual({
      generation: 2,
      scoreDelta: 0.3,
      changes: [{ component: "playbook" }],
      metadata: {},
    });

    expect(normalizeAttributionResultData({ credits: { playbook: "0.2" } })).toMatchObject({
      generation: 0,
      totalDelta: 0,
      credits: { playbook: 0.2 },
    });

    expect(computeTotalChangeMagnitude([{ magnitude: 0.1234567 }, { magnitude: 0.2 }])).toBe(0.323457);
    expect(buildZeroCredits([{ component: "playbook" }, { component: "tools" }])).toEqual({
      playbook: 0,
      tools: 0,
    });
  });

  it("round-trips credit assignment records through class serialization", () => {
    const record = new CreditAssignmentRecord(
      "run-1",
      3,
      new GenerationChangeVector(3, 0.3, [
        new ComponentChange("playbook", 0.6, "changed"),
      ]),
      new AttributionResult(3, 0.3, { playbook: 0.3 }),
      { source: "test" },
    );

    const dict = record.toDict();
    expect(normalizeCreditAssignmentRecordData(dict)).toMatchObject({
      runId: "run-1",
      generation: 3,
      metadata: { source: "test" },
    });

    const restored = CreditAssignmentRecord.fromDict(dict);
    expect(restored.toDict()).toEqual(dict);
  });
});
