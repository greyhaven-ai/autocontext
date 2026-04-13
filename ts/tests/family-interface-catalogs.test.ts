import { describe, expect, it } from "vitest";

import {
  buildFamilyInterfaceDetectorOrder,
  buildFamilyInterfaceGuardCatalog,
} from "../src/scenarios/family-interface-catalogs.js";

describe("family interface catalogs", () => {
  const guards = {
    isGameScenario: () => false,
    isAgentTask: () => true,
    isSimulation: () => true,
    isNegotiation: () => true,
    isInvestigation: () => true,
    isWorkflow: () => true,
    isSchemaEvolution: () => true,
    isToolFragility: () => true,
    isOperatorLoop: () => true,
    isCoordination: () => true,
    isArtifactEditing: () => true,
  };

  it("builds the family-interface guard catalog", () => {
    const catalog = buildFamilyInterfaceGuardCatalog(guards);

    expect(catalog.game).toBe(guards.isGameScenario);
    expect(catalog.agent_task).toBe(guards.isAgentTask);
    expect(catalog.coordination).toBe(guards.isCoordination);
    expect(catalog.artifact_editing).toBe(guards.isArtifactEditing);
  });

  it("builds the ordered detector declaration for runtime family detection", () => {
    const detectors = buildFamilyInterfaceDetectorOrder(guards);

    expect(detectors.map(([family]) => family)).toEqual([
      "game",
      "artifact_editing",
      "negotiation",
      "investigation",
      "workflow",
      "schema_evolution",
      "tool_fragility",
      "operator_loop",
      "coordination",
      "simulation",
      "agent_task",
    ]);
  });
});
