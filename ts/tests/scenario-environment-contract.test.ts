import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  GridCtfScenario,
  SCENARIO_ENVIRONMENT_HOOK_KINDS,
  ScenarioEnvironmentContractSchema,
  TemplateLoader,
  scenarioEnvironmentContractForGame,
} from "../src/scenarios/index.js";

const CONTRACT_SCHEMA = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "scenario-environment-contract.json"),
    "utf-8",
  ),
) as {
  required: string[];
  properties: { hooks: { required: string[] } };
  $defs: { hookKind: { enum: string[] } };
};

describe("ScenarioEnvironmentContract", () => {
  it("keeps the docs schema aligned with the TypeScript hook vocabulary", () => {
    expect(CONTRACT_SCHEMA.required).toEqual([
      "schema_version",
      "scenario_name",
      "scenario_family",
      "hooks",
    ]);
    expect(CONTRACT_SCHEMA.properties.hooks.required).toEqual([...SCENARIO_ENVIRONMENT_HOOK_KINDS]);
    expect(CONTRACT_SCHEMA.$defs.hookKind.enum).toEqual([...SCENARIO_ENVIRONMENT_HOOK_KINDS]);
  });

  it("reports a uniform environment contract for game scenarios", () => {
    const contract = scenarioEnvironmentContractForGame(new GridCtfScenario());

    expect(contract.scenario_name).toBe("grid_ctf");
    expect(contract.scenario_family).toBe("game");
    expect(contract.hooks.reset.length).toBeGreaterThan(0);
    expect(contract.hooks.rollout.length).toBeGreaterThan(0);
    expect(contract.hooks.verification.length).toBeGreaterThan(0);
    expect(contract.hooks.scoring[0]?.emits).toEqual(["scalar_score"]);
    expect(contract.hooks.replay[0]?.emits).toEqual(["replay_timeline"]);

    expect(ScenarioEnvironmentContractSchema.parse(contract)).toEqual(contract);
  });

  it("exposes the environment contract on a scenario template", () => {
    const spec = new TemplateLoader().getTemplate("content-generation");

    expect(spec.environmentContract?.scenario_family).toBe("agent_task");
    expect(spec.environmentContract?.hooks.verification[0]?.kind).toBe("verification");
    expect(spec.environmentContract?.hooks.evidence[0]?.emits).toEqual([
      "judge_reasoning",
      "dimension_scores",
    ]);
  });
});
