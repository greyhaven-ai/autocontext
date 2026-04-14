import { describe, expect, it, vi } from "vitest";

import { registerScenarioCatalogTools } from "../src/mcp/scenario-catalog-tools.js";

function createFakeServer() {
  const registeredTools: Record<
    string,
    {
      description: string;
      schema: Record<string, unknown>;
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>;
    }
  > = {};

  return {
    registeredTools,
    tool(
      name: string,
      description: string,
      schema: Record<string, unknown>,
      handler: (args: Record<string, unknown>) => Promise<{ content: Array<{ type: string; text: string }> }>,
    ) {
      registeredTools[name] = { description, schema, handler };
    },
  };
}

class GridCtfScenarioStub {
  describeRules() {
    return "Capture the flag";
  }

  describeStrategyInterface() {
    return "{ aggression, defense, path_bias }";
  }

  describeEvaluationCriteria() {
    return "Win with the highest score";
  }

  scoringDimensions() {
    return ["score", "speed"];
  }
}

class OthelloScenarioStub {
  describeRules() {
    return "Flip the board";
  }

  describeStrategyInterface() {
    return "{ mobility_weight, corner_weight }";
  }

  describeEvaluationCriteria() {
    return "Maximize disk advantage";
  }
}

describe("scenario catalog MCP tools", () => {
  it("registers list_scenarios with sorted metadata payloads", async () => {
    const server = createFakeServer();

    registerScenarioCatalogTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          zeta: OthelloScenarioStub,
          alpha: GridCtfScenarioStub,
        }),
      },
    });

    const result = await server.registeredTools.list_scenarios.handler({});
    expect(JSON.parse(result.content[0].text)).toEqual({
      scenarios: [
        {
          name: "alpha",
          rules: "Capture the flag",
          strategyInterface: "{ aggression, defense, path_bias }",
        },
        {
          name: "zeta",
          rules: "Flip the board",
          strategyInterface: "{ mobility_weight, corner_weight }",
        },
      ],
    });
  });

  it("returns detailed scenario payloads and enforces the game family contract", async () => {
    const server = createFakeServer();
    const assertFamilyContract = vi.fn();

    registerScenarioCatalogTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          grid_ctf: GridCtfScenarioStub,
        }),
        assertFamilyContract,
      },
    });

    const result = await server.registeredTools.get_scenario.handler({
      name: "grid_ctf",
    });

    expect(assertFamilyContract).toHaveBeenCalledOnce();
    expect(assertFamilyContract).toHaveBeenCalledWith(
      expect.any(GridCtfScenarioStub),
      "game",
      "scenario 'grid_ctf'",
    );
    expect(JSON.parse(result.content[0].text)).toEqual({
      name: "grid_ctf",
      rules: "Capture the flag",
      strategyInterface: "{ aggression, defense, path_bias }",
      evaluationCriteria: "Win with the highest score",
      scoringDimensions: ["score", "speed"],
    });
  });

  it("returns stable unknown-scenario errors", async () => {
    const server = createFakeServer();

    registerScenarioCatalogTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          grid_ctf: GridCtfScenarioStub,
        }),
      },
    });

    const result = await server.registeredTools.get_scenario.handler({
      name: "missing",
    });

    expect(JSON.parse(result.content[0].text)).toEqual({
      error: "Unknown scenario: missing",
    });
  });
});
