import { describe, expect, it } from "vitest";

import {
  buildCompetitorStrategyRepairPrompt,
  buildGenerationAttemptCandidate,
  CompetitorStrategyParseError,
  createTournamentExecutionPlan,
  parseCompetitorStrategyResult,
} from "../src/loop/generation-execution-step.js";

describe("generation execution step", () => {
  it("parses competitor strategy JSON when valid", () => {
    expect(
      parseCompetitorStrategyResult('{"aggression":0.8,"defense":0.4,"path_bias":0.2}'),
    ).toEqual({
      aggression: 0.8,
      defense: 0.4,
      path_bias: 0.2,
    });
  });

  it("fails closed when competitor output is not a JSON object", () => {
    expect(() => parseCompetitorStrategyResult("not-json")).toThrow(CompetitorStrategyParseError);
    expect(() => parseCompetitorStrategyResult("[]")).toThrow(
      "Competitor strategy must be a JSON object",
    );
  });

  it("bounds the strategy context and invalid output in repair prompts", () => {
    const prompt = buildCompetitorStrategyRepairPrompt({
      competitorPrompt: `context-start-${"a".repeat(10_000)}-context-end`,
      invalidOutput: `output-start-${"b".repeat(6_000)}-output-end`,
    });

    expect(prompt).toContain("context-start-");
    expect(prompt).not.toContain("context-end");
    expect(prompt).toContain("output-start-");
    expect(prompt).not.toContain("output-end");
    expect(prompt.length).toBeLessThan(12_500);
  });

  it("creates tournament execution plan from generation context", () => {
    expect(
      createTournamentExecutionPlan({
        generation: 3,
        seedBase: 1000,
        matchesPerGeneration: 4,
        currentElo: 1075,
      }),
    ).toEqual({
      seedForGeneration: 1008,
      tournamentOptions: {
        matchCount: 4,
        seedBase: 1008,
        initialElo: 1075,
      },
    });
  });

  it("builds a generation attempt candidate from execution outputs", () => {
    const tournamentResult = {
      matches: [],
      meanScore: 0.66,
      bestScore: 0.71,
      wins: 2,
      losses: 1,
      elo: 1033,
    };

    expect(
      buildGenerationAttemptCandidate({
        competitorPrompt: "prompt",
        competitorResultText: '{"aggression":0.6}',
        strategy: { aggression: 0.6 },
        tournamentResult,
        gateDecision: "advance",
      }),
    ).toEqual({
      competitorPrompt: "prompt",
      competitorResultText: '{"aggression":0.6}',
      strategy: { aggression: 0.6 },
      tournamentResult,
      gateDecision: "advance",
    });
  });
});
