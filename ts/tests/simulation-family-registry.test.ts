import { describe, expect, it } from "vitest";

import { SIMULATION_FAMILY_GUARDS } from "../src/scenarios/simulation-family-registry.js";

describe("simulation family registry", () => {
  it("exports the derived simulation-family guard registry", () => {
    expect(SIMULATION_FAMILY_GUARDS).toMatchObject({
      simulation: expect.any(Function),
      negotiation: expect.any(Function),
      investigation: expect.any(Function),
      workflow: expect.any(Function),
      schemaEvolution: expect.any(Function),
      toolFragility: expect.any(Function),
      operatorLoop: expect.any(Function),
      coordination: expect.any(Function),
    });
  });
});
