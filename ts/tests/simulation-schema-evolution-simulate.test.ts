import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { SimulationEngine } from "../src/simulation/engine.js";
import type { LLMProvider } from "../src/types/index.js";

const AC277_PROMPT =
  "Harness Stress Test AC-277: portfolio construction under macroeconomic regime change with schema evolution. " +
  "Manage allocations across equities, bonds, cash, commodities, and hedges while regimes shift from expansion to inflation shock to recession. " +
  "Track Sharpe, drawdown, exposure limits, turnover, regime detection speed, delayed effects, breaking schema mutations, stale-assumption detection, " +
  "and adaptation speed after each mutation.";

function genericZeroMutationSpec(): string {
  return JSON.stringify({
    description: "Portfolio construction under macro regime change",
    environment_description: "Portfolio optimizer with changing market data payloads",
    initial_state_description: "Expansion regime with v1 allocation metrics",
    success_criteria: ["adapt allocations", "detect stale assumptions"],
    failure_modes: ["uses stale fields"],
    max_steps: 6,
    actions: [
      {
        name: "inspect_market_payload",
        description: "Inspect the latest market payload",
        parameters: {},
        preconditions: [],
        effects: ["payload_seen"],
      },
      {
        name: "rebalance_portfolio",
        description: "Rebalance after schema review",
        parameters: {},
        preconditions: ["payload_seen"],
        effects: ["portfolio_rebalanced"],
      },
    ],
    mutations: [],
  });
}

function schemaEvolutionSpec(mutations: unknown[]): string {
  return `<!-- SCHEMA_EVOLUTION_SPEC_START -->
${JSON.stringify({
  description: "Portfolio construction under macro regime change with evolving schemas",
  environment_description: "Risk and allocation feeds change shape as regimes shift",
  initial_state_description: "v1 feed reports sharpe, drawdown, and exposure limits",
  mutations,
  success_criteria: [
    "detect each schema version change",
    "discard stale assumptions after breaking mutations",
  ],
  failure_modes: ["continues to read removed fields", "ignores regime-specific payload changes"],
  max_steps: 6,
  actions: [
    {
      name: "inspect_market_payload",
      description: "Inspect the latest market payload",
      parameters: {},
      preconditions: [],
      effects: ["payload_seen"],
    },
    {
      name: "rebalance_portfolio",
      description: "Rebalance after schema review",
      parameters: {},
      preconditions: ["payload_seen"],
      effects: ["portfolio_rebalanced"],
    },
  ],
})}
<!-- SCHEMA_EVOLUTION_SPEC_END -->`;
}

function providerForSchemaDesigner(text: string): LLMProvider {
  return {
    name: "test-pi",
    defaultModel: () => "test-model",
    complete: async ({ systemPrompt }) => {
      if (systemPrompt.includes("SchemaEvolutionSpec")) {
        return { text };
      }
      return { text: genericZeroMutationSpec() };
    },
  };
}

describe("schema-evolution simulate materialization", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-schema-sim-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("uses the schema-evolution designer and persists real AC-277 mutations", async () => {
    const provider = providerForSchemaDesigner(
      schemaEvolutionSpec([
        {
          version: 2,
          description: "Inflation shock renames exposure_limit to risk_budget",
          breaking: true,
          fields_added: ["risk_budget"],
          fields_removed: ["exposure_limit"],
          fields_modified: { turnover: "daily_number -> rolling_window_object" },
        },
        {
          version: 3,
          description: "Recession feed removes sharpe and adds downside_capture",
          breaking: true,
          fields_added: ["downside_capture"],
          fields_removed: ["sharpe"],
          fields_modified: { drawdown: "percent -> basis_points" },
        },
      ]),
    );

    const result = await new SimulationEngine(provider, tmpDir).run({
      description: AC277_PROMPT,
      saveAs: "ac277_schema_evolution",
      runs: 1,
      maxSteps: 4,
    });

    expect(result.status).not.toBe("failed");
    expect(result.family).toBe("schema_evolution");

    const specPath = join(tmpDir, "_simulations", "ac277_schema_evolution", "spec.json");
    const spec = JSON.parse(readFileSync(specPath, "utf-8")) as { mutations?: unknown[] };
    expect(spec.mutations).toHaveLength(2);
    expect(
      spec.mutations?.some(
        (mutation) =>
          typeof mutation === "object" &&
          mutation !== null &&
          (mutation as { breaking?: unknown }).breaking === true,
      ),
    ).toBe(true);
  });

  it("fails before persisting schema-evolution artifacts when mutations are empty", async () => {
    const provider = providerForSchemaDesigner(schemaEvolutionSpec([]));

    const result = await new SimulationEngine(provider, tmpDir).run({
      description: AC277_PROMPT,
      saveAs: "ac277_empty_mutations",
      runs: 1,
    });

    expect(result.status).toBe("failed");
    expect(result.error).toMatch(/mutations/i);
    expect(
      existsSync(join(tmpDir, "_simulations", "ac277_empty_mutations", "spec.json")),
    ).toBe(false);
  });
});
