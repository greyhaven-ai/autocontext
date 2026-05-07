import { describe, expect, it } from "vitest";

import { executeGeneratedInvestigation } from "../src/investigation/investigation-execution-workflow.js";

describe("investigation execution workflow", () => {
  it("executes generated scenarios with maxSteps limits and normalizes collected evidence", async () => {
    const source = `
module.exports.scenario = {
  initialState() {
    return { turn: 0, collectedEvidence: [] };
  },
  isTerminal(state) {
    return state.turn >= 3;
  },
  getAvailableActions() {
    return [{ name: "inspect" }];
  },
  executeAction(state, action) {
    return {
      result: { action },
      state: {
        turn: state.turn + 1,
        collectedEvidence: [
          ...(state.collectedEvidence || []),
          {
            summary: "Database saturation detected",
            isRedHerring: false,
            relevance: 0.9,
          },
        ],
      },
    };
  },
};
`;

    await expect(
      executeGeneratedInvestigation({ source, maxSteps: 1 }),
    ).resolves.toEqual({
      stepsExecuted: 1,
      collectedEvidence: [
        {
          id: "collected_0",
          content: "Database saturation detected",
          isRedHerring: false,
          relevance: 0.9,
        },
      ],
      finalState: {
        turn: 1,
        collectedEvidence: [
          {
            summary: "Database saturation detected",
            isRedHerring: false,
            relevance: 0.9,
          },
        ],
      },
    });
  });

  it("uses the first non-empty evidence text when generated content is blank", async () => {
    const source = `
module.exports.scenario = {
  initialState() {
    return {
      collectedEvidence: [
        { id: "fallback-id", content: "", summary: "Config drift observed", relevance: 0.7 },
        { id: "", content: "   ", summary: "", relevance: 0.2 },
      ],
    };
  },
  isTerminal() {
    return true;
  },
  getAvailableActions() {
    return [];
  },
  executeAction(state) {
    return { result: {}, state };
  },
};
`;

    const result = await executeGeneratedInvestigation({ source });

    expect(result.collectedEvidence).toEqual([
      {
        id: "fallback-id",
        content: "Config drift observed",
        isRedHerring: false,
        relevance: 0.7,
      },
      {
        id: "collected_1",
        content: "unknown",
        isRedHerring: false,
        relevance: 0.2,
      },
    ]);
  });
});
