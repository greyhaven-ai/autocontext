import { describe, expect, it } from "vitest";

import {
  buildDefaultFamilyClassification,
  buildRankedFamilyClassification,
  buildRationale,
  scoreSignals,
} from "../src/scenarios/family-classifier-scoring.js";

describe("family classifier scoring workflow", () => {
  it("scores matched signals and builds rationale text", () => {
    const [score, matched] = scoreSignals(
      "deploy a pipeline with rollback and incident triage",
      { deploy: 1.5, rollback: 2.0, triage: 1.0, essay: 2.0 },
    );

    expect(score).toBe(4.5);
    expect(matched).toEqual(["deploy", "rollback", "triage"]);
    expect(buildRationale(matched, "simulation")).toBe(
      "Matched simulation signals: deploy, rollback, triage",
    );
    expect(buildRationale([], "agent_task")).toBe("No strong signals for agent_task");
  });

  it("builds default and ranked classifications with normalized alternatives", () => {
    expect(buildDefaultFamilyClassification(["game", "agent_task", "simulation"])).toMatchObject({
      familyName: "agent_task",
      confidence: 0.2,
    });

    const classification = buildRankedFamilyClassification({
      families: ["simulation", "agent_task", "workflow"],
      rawScores: new Map([
        ["simulation", 3],
        ["agent_task", 1],
        ["workflow", 2],
      ]),
      matchedSignals: new Map([
        ["simulation", ["deploy", "rollback"]],
        ["agent_task", ["essay"]],
        ["workflow", ["transaction"]],
      ]),
      total: 6,
    });

    expect(classification.familyName).toBe("simulation");
    expect(classification.confidence).toBe(0.5);
    expect(classification.alternatives[0]).toMatchObject({
      familyName: "workflow",
      confidence: 0.3333,
    });
  });
});
