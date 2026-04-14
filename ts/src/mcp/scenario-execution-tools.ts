import { z } from "zod";

import { TournamentRunner } from "../execution/tournament.js";
import type { ScenarioInterface } from "../scenarios/game-interface.js";

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

interface ScenarioExecutionResult {
  score: number;
  winner?: string | null;
  passedValidation?: boolean;
  validationErrors?: string[];
}

type ScenarioExecutionConstructor = new () => ScenarioInterface;
type ScenarioExecutionRegistry = Record<string, ScenarioExecutionConstructor>;

interface ScenarioExecutionInternals {
  loadScenarioRegistry(): Promise<ScenarioExecutionRegistry>;
  createTournamentRunner(
    scenario: ScenarioInterface,
    opts: { matchCount: number; seedBase: number },
  ): {
    run(strategy: Record<string, unknown>): {
      meanScore: number;
      bestScore: number;
      elo: number;
      wins: number;
      losses: number;
    };
  };
}

const defaultInternals: ScenarioExecutionInternals = {
  loadScenarioRegistry: async () => {
    const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
    return SCENARIO_REGISTRY;
  },
  createTournamentRunner: (scenario, opts) =>
    new TournamentRunner(scenario, opts),
};

function jsonText(payload: unknown, indent?: number): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
      },
    ],
  };
}

function resolveScenario(
  registry: ScenarioExecutionRegistry,
  name: string,
): ScenarioInterface | JsonToolResponse {
  const ScenarioClass = registry[name];
  if (!ScenarioClass) {
    return jsonText({ error: `Unknown scenario: ${name}` });
  }
  return new ScenarioClass();
}

function parseStrategy(strategy: string): Record<string, unknown> | JsonToolResponse {
  try {
    const parsed: unknown = JSON.parse(strategy);
    if (!isRecord(parsed)) {
      return jsonText({ error: "Invalid JSON" });
    }
    return parsed;
  } catch {
    return jsonText({ error: "Invalid JSON" });
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const ScenarioStrategyArgsSchema = z.object({
  scenario: z.string(),
  strategy: z.string(),
});
type ScenarioStrategyArgs = z.infer<typeof ScenarioStrategyArgsSchema>;

const RunMatchArgsSchema = ScenarioStrategyArgsSchema.extend({
  seed: z.number().int().default(42),
});
type RunMatchArgs = z.infer<typeof RunMatchArgsSchema>;

const RunTournamentArgsSchema = ScenarioStrategyArgsSchema.extend({
  matches: z.number().int().default(3),
  seedBase: z.number().int().default(1000),
});
type RunTournamentArgs = z.infer<typeof RunTournamentArgsSchema>;

export function registerScenarioExecutionTools(
  server: McpToolRegistrar,
  opts?: {
    internals?: Partial<ScenarioExecutionInternals>;
  },
): void {
  const internals: ScenarioExecutionInternals = {
    ...defaultInternals,
    ...opts?.internals,
  };

  server.tool(
    "validate_strategy",
    "Validate a strategy JSON against a scenario's constraints",
    ScenarioStrategyArgsSchema.shape,
    async (args: ScenarioStrategyArgs) => {
      const registry = await internals.loadScenarioRegistry();
      const scenario = resolveScenario(registry, args.scenario);
      if ("content" in scenario) {
        return scenario;
      }

      const strategy = parseStrategy(args.strategy);
      if ("content" in strategy) {
        return jsonText({ valid: false, reason: "Invalid JSON" });
      }

      const [valid, reason] = scenario.validateActions(
        scenario.initialState(42),
        "challenger",
        strategy,
      );
      return jsonText({ valid, reason });
    },
  );

  server.tool(
    "run_match",
    "Execute a single match for a scenario",
    RunMatchArgsSchema.shape,
    async (args: RunMatchArgs) => {
      const registry = await internals.loadScenarioRegistry();
      const scenario = resolveScenario(registry, args.scenario);
      if ("content" in scenario) {
        return scenario;
      }

      const strategy = parseStrategy(args.strategy);
      if ("content" in strategy) {
        return strategy;
      }

      return jsonText(
        scenario.executeMatch(strategy, args.seed),
        2,
      );
    },
  );

  server.tool(
    "run_tournament",
    "Run N matches with Elo scoring",
    RunTournamentArgsSchema.shape,
    async (args: RunTournamentArgs) => {
      const registry = await internals.loadScenarioRegistry();
      const scenario = resolveScenario(registry, args.scenario);
      if ("content" in scenario) {
        return scenario;
      }

      const strategy = parseStrategy(args.strategy);
      if ("content" in strategy) {
        return strategy;
      }

      const result = internals.createTournamentRunner(scenario, {
        matchCount: args.matches,
        seedBase: args.seedBase,
      }).run(strategy);

      return jsonText(
        {
          meanScore: result.meanScore,
          bestScore: result.bestScore,
          elo: result.elo,
          wins: result.wins,
          losses: result.losses,
        },
        2,
      );
    },
  );
}
