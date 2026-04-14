import { z } from "zod";

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

interface ScenarioCatalogEntry {
  describeRules(): string;
  describeStrategyInterface(): string;
  describeEvaluationCriteria(): string;
  scoringDimensions?(): unknown;
}

type ScenarioCatalogConstructor = new () => ScenarioCatalogEntry;
type ScenarioRegistry = Record<string, ScenarioCatalogConstructor>;

interface ScenarioCatalogInternals {
  loadScenarioRegistry(): Promise<ScenarioRegistry>;
  assertFamilyContract(
    scenario: ScenarioCatalogEntry,
    family: "game",
    context: string,
  ): void;
}

const defaultInternals: ScenarioCatalogInternals = {
  loadScenarioRegistry: async () => {
    const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
    return SCENARIO_REGISTRY as ScenarioRegistry;
  },
  assertFamilyContract: () => undefined,
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

export function registerScenarioCatalogTools(
  server: McpToolRegistrar,
  opts?: {
    internals?: Partial<ScenarioCatalogInternals>;
  },
): void {
  const internals: ScenarioCatalogInternals = {
    ...defaultInternals,
    ...opts?.internals,
  };

  server.tool(
    "list_scenarios",
    "List available scenarios with metadata",
    {},
    async () => {
      const registry = await internals.loadScenarioRegistry();
      const scenarios = Object.keys(registry)
        .sort()
        .map((name) => {
          const instance = new registry[name]();
          return {
            name,
            rules: instance.describeRules(),
            strategyInterface: instance.describeStrategyInterface(),
          };
        });
      return jsonText({ scenarios }, 2);
    },
  );

  server.tool(
    "get_scenario",
    "Get detailed information about a scenario",
    {
      name: z.string().describe("Scenario name"),
    },
    async (args: Record<string, unknown>) => {
      const registry = await internals.loadScenarioRegistry();
      const name = args.name as string;
      const ScenarioClass = registry[name];
      if (!ScenarioClass) {
        return jsonText({ error: `Unknown scenario: ${name}` });
      }

      const instance = new ScenarioClass();
      internals.assertFamilyContract(instance, "game", `scenario '${name}'`);
      return jsonText(
        {
          name,
          rules: instance.describeRules(),
          strategyInterface: instance.describeStrategyInterface(),
          evaluationCriteria: instance.describeEvaluationCriteria(),
          scoringDimensions: instance.scoringDimensions?.() ?? null,
        },
        2,
      );
    },
  );
}
