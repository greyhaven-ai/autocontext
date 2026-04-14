import { describe, expect, it } from "vitest";

import {
  extractPromptSections,
  formatPromptTrajectory,
  measurePromptWordOverlap,
  readPromptContextString,
} from "../src/training/prompt-alignment-helpers.js";

describe("prompt alignment helpers workflow", () => {
  it("reads preferred context strings and formats trajectory rows", () => {
    expect(readPromptContextString({ scenarioRules: "  Capture the flag  " }, "scenarioRules")).toBe("Capture the flag");
    expect(readPromptContextString({ scenario_rules: "snake_case" }, "scenarioRules", "scenario_rules")).toBe("snake_case");
    expect(readPromptContextString({}, "missing")).toBe("");

    expect(formatPromptTrajectory([
      { generation_index: 1, best_score: 0.65, gate_decision: "advance" },
      { best_score: 0.78, gate_decision: "retry" },
      null,
    ])).toBe([
      "Generation 1: score=0.6500, gate=advance",
      "Generation 2: score=0.7800, gate=retry",
    ].join("\n"));
  });

  it("extracts known prompt sections and measures user prompt similarity", () => {
    expect(extractPromptSections([
      "## Scenario Rules",
      "Game rules",
      "",
      "### Evaluation Criteria",
      "Maximize score",
      "",
      "**Playbook**",
      "Tips",
    ].join("\n"))).toEqual([
      "Scenario Rules",
      "Evaluation Criteria",
      "Playbook",
    ]);

    expect(measurePromptWordOverlap("produce a strategy", "produce a strategy now")).toBeGreaterThanOrEqual(0.75);
    expect(measurePromptWordOverlap("totally different words", "nothing overlaps here")).toBe(0);
  });
});
