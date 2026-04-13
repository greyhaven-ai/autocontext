import { describe, expect, it, vi } from "vitest";

import type {
  ActionDict,
  HarnessLoaderLike,
  ScenarioLike,
} from "../src/execution/action-filter.js";
import {
  getHarnessActions,
  getLegalActions,
} from "../src/execution/action-filter-discovery-workflow.js";
import {
  formatActionPrompt,
  isContinuousParamSpace,
} from "../src/execution/action-filter-prompt-workflow.js";
import {
  extractJsonObject,
  parseActionSelection,
} from "../src/execution/action-filter-selection-workflow.js";
import { getVerifyFeedback } from "../src/execution/action-filter-verification-workflow.js";

function buildScenario(actions: ActionDict[] | null): ScenarioLike {
  return {
    enumerateLegalActions: vi.fn().mockReturnValue(actions),
    validateActions: vi.fn().mockReturnValue([true, "ok"]),
  };
}

describe("action filter workflows", () => {
  it("discovers legal actions from scenario first, then harness validators", () => {
    const scenarioActions = [{ action: "move", description: "Move forward" }];
    const scenario = buildScenario(scenarioActions);
    const loader: HarnessLoaderLike = {
      validators: [
        {
          enumerate_legal_actions: vi.fn().mockReturnValue([
            { action: "fallback", description: "Harness fallback" },
          ]),
        },
      ],
    };

    expect(getLegalActions(scenario, {}, loader)).toEqual(scenarioActions);

    const fallbackScenario = buildScenario(null);
    expect(getLegalActions(fallbackScenario, {}, loader)).toEqual([
      { action: "fallback", description: "Harness fallback" },
    ]);
    expect(getHarnessActions(null, {})).toBeNull();
  });

  it("formats discrete and continuous action prompts", () => {
    expect(
      formatActionPrompt([
        { action: "capture_flag", description: "Capture", row: 1, col: 5 },
      ]),
    ).toContain("1. capture_flag — Capture (row 1, col 5)");

    const continuous = [
      { action: "aggression", description: "Tune aggression", type: "continuous", range: [0, 1] },
      { action: "defense", description: "Tune defense", type: "continuous", range: [0, 1] },
    ] satisfies ActionDict[];

    expect(isContinuousParamSpace(continuous)).toBe(true);
    const prompt = formatActionPrompt(continuous);
    expect(prompt).toContain("Provide a JSON object with all strategy parameters:");
    expect(prompt).toContain('"aggression":0.5');
    expect(prompt).toContain("Respond with JSON only.");
  });

  it("parses indexed, named, and JSON continuous selections", () => {
    const actions = [
      { action: "move_up", description: "Move up" },
      { action: "move_down", description: "Move down" },
    ] satisfies ActionDict[];

    expect(parseActionSelection("2", actions)).toEqual(actions[1]);
    expect(parseActionSelection("I choose move_up", actions)).toEqual(actions[0]);

    const continuous = [
      { action: "aggression", description: "x", type: "continuous", range: [0, 1] },
      { action: "defense", description: "y", type: "continuous", range: [0, 1] },
    ] satisfies ActionDict[];
    expect(
      parseActionSelection("```json\n{\"aggression\":0.6,\"defense\":0.4}\n```", continuous),
    ).toEqual({ aggression: 0.6, defense: 0.4 });
    expect(extractJsonObject("prefix {\"a\":1} suffix")).toEqual({ a: 1 });
    expect(parseActionSelection('{"aggression":2,"defense":0.4}', continuous)).toBeNull();
  });

  it("builds verification feedback with prompt text when legal actions exist", () => {
    expect(getVerifyFeedback("bad move", null)).toBe("Invalid action: bad move\nPlease try again.");
    const feedback = getVerifyFeedback("bad move", [
      { action: "move_up", description: "Move up" },
    ]);
    expect(feedback).toContain("Invalid action: bad move");
    expect(feedback).toContain("Available actions:");
    expect(feedback).toContain("Please try again.");
  });
});
