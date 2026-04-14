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
});
