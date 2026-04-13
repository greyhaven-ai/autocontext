import { describe, expect, it } from "vitest";

import {
  FAMILY_INTERFACE_DETECTOR_ORDER,
  FAMILY_INTERFACE_GUARD_CATALOG,
  FAMILY_INTERFACE_GUARDS,
} from "../src/scenarios/family-interface-registry.js";

describe("family interface registry", () => {
  it("exports the public family guard registry", () => {
    expect(FAMILY_INTERFACE_GUARDS).toMatchObject({
      isGameScenario: expect.any(Function),
      isAgentTask: expect.any(Function),
      isSimulation: expect.any(Function),
      isNegotiation: expect.any(Function),
      isInvestigation: expect.any(Function),
      isWorkflow: expect.any(Function),
      isSchemaEvolution: expect.any(Function),
      isToolFragility: expect.any(Function),
      isOperatorLoop: expect.any(Function),
      isCoordination: expect.any(Function),
      isArtifactEditing: expect.any(Function),
    });
  });

  it("derives the runtime family guard catalog and detector order", () => {
    expect(FAMILY_INTERFACE_GUARD_CATALOG).toMatchObject({
      game: expect.any(Function),
      agent_task: expect.any(Function),
      simulation: expect.any(Function),
      negotiation: expect.any(Function),
      investigation: expect.any(Function),
      workflow: expect.any(Function),
      schema_evolution: expect.any(Function),
      tool_fragility: expect.any(Function),
      operator_loop: expect.any(Function),
      coordination: expect.any(Function),
      artifact_editing: expect.any(Function),
    });

    expect(FAMILY_INTERFACE_DETECTOR_ORDER.map(([family]) => family)).toEqual([
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
