import { describe, expect, it, vi } from "vitest";

import { registerScenarioExecutionTools } from "../src/mcp/scenario-execution-tools.js";

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

class ScenarioStub {
  initialState(seed: number) {
    return { seed };
  }

  validateActions(
    _state: unknown,
    actor: string,
    strategy: Record<string, unknown>,
  ): [boolean, string] {
    return [
      actor === "challenger" && strategy.aggression === 0.6,
      actor === "challenger" && strategy.aggression === 0.6 ? "ok" : "bad strategy",
    ];
  }

  executeMatch(strategy: Record<string, unknown>, seed: number) {
    return {
      score: Number(strategy.aggression ?? 0),
      winner: seed === 7 ? "challenger" : "defender",
      passedValidation: true,
      validationErrors: [],
    };
  }
}

describe("scenario execution MCP tools", () => {
  it("validates strategies against the selected scenario", async () => {
    const server = createFakeServer();

    registerScenarioExecutionTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          grid_ctf: ScenarioStub,
        }),
      },
    });

    const result = await server.registeredTools.validate_strategy.handler({
      scenario: "grid_ctf",
      strategy: JSON.stringify({ aggression: 0.6 }),
    });

    expect(JSON.parse(result.content[0].text)).toEqual({
      valid: true,
      reason: "ok",
    });
  });

  it("returns stable errors for unknown scenarios and invalid JSON", async () => {
    const server = createFakeServer();

    registerScenarioExecutionTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          grid_ctf: ScenarioStub,
        }),
      },
    });

    const missingScenario = await server.registeredTools.validate_strategy.handler({
      scenario: "missing",
      strategy: JSON.stringify({ aggression: 0.6 }),
    });
    expect(JSON.parse(missingScenario.content[0].text)).toEqual({
      error: "Unknown scenario: missing",
    });

    const invalidJson = await server.registeredTools.validate_strategy.handler({
      scenario: "grid_ctf",
      strategy: "{not-json}",
    });
    expect(JSON.parse(invalidJson.content[0].text)).toEqual({
      valid: false,
      reason: "Invalid JSON",
    });
  });

  it("runs a single match with parsed strategy payloads", async () => {
    const server = createFakeServer();

    registerScenarioExecutionTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          grid_ctf: ScenarioStub,
        }),
      },
    });

    const result = await server.registeredTools.run_match.handler({
      scenario: "grid_ctf",
      strategy: JSON.stringify({ aggression: 0.7 }),
      seed: 7,
    });

    expect(JSON.parse(result.content[0].text)).toEqual({
      score: 0.7,
      winner: "challenger",
      passedValidation: true,
      validationErrors: [],
    });
  });

  it("runs tournaments and returns the summarized tournament payload", async () => {
    const server = createFakeServer();
    const run = vi.fn(() => ({
      meanScore: 0.74,
      bestScore: 0.91,
      elo: 1112,
      wins: 2,
      losses: 1,
      matches: [],
    }));
    const createTournamentRunner = vi.fn(() => ({ run }));

    registerScenarioExecutionTools(server, {
      internals: {
        loadScenarioRegistry: async () => ({
          grid_ctf: ScenarioStub,
        }),
        createTournamentRunner,
      },
    });

    const result = await server.registeredTools.run_tournament.handler({
      scenario: "grid_ctf",
      strategy: JSON.stringify({ aggression: 0.6 }),
      matches: 3,
      seedBase: 1000,
    });

    expect(createTournamentRunner).toHaveBeenCalledOnce();
    expect(run).toHaveBeenCalledWith({ aggression: 0.6 });
    expect(JSON.parse(result.content[0].text)).toEqual({
      meanScore: 0.74,
      bestScore: 0.91,
      elo: 1112,
      wins: 2,
      losses: 1,
    });
  });
});
