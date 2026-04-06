/**
 * AC-531: SIMULATION_FAMILIES must include all simulation-like families
 * (action-based execution model). Excludes game, agent_task, artifact_editing
 * which use different execution models.
 */

import { describe, it, expect } from "vitest";
import { SIMULATION_FAMILIES } from "../src/simulation/engine.js";
import { SIMULATION_LIKE_FAMILIES } from "../src/scenarios/families.js";

// ---------------------------------------------------------------------------
// All simulation-like families must be in SIMULATION_FAMILIES
// ---------------------------------------------------------------------------

describe("SIMULATION_FAMILIES completeness (AC-531)", () => {
  const EXPECTED: string[] = [
    "simulation",
    "investigation",
    "workflow",
    "negotiation",
    "schema_evolution",
    "tool_fragility",
    "operator_loop",
    "coordination",
  ];

  it("contains exactly the 8 simulation-like families", () => {
    expect(SIMULATION_FAMILIES.size).toBe(8);
    for (const family of EXPECTED) {
      expect(SIMULATION_FAMILIES.has(family)).toBe(true);
    }
  });

  it("does NOT contain game, agent_task, or artifact_editing", () => {
    expect(SIMULATION_FAMILIES.has("game")).toBe(false);
    expect(SIMULATION_FAMILIES.has("agent_task")).toBe(false);
    expect(SIMULATION_FAMILIES.has("artifact_editing")).toBe(false);
  });

  it("is the same object as SIMULATION_LIKE_FAMILIES from families.ts (DRY)", () => {
    // Engine re-exports the canonical set — no duplication
    expect(SIMULATION_FAMILIES).toBe(SIMULATION_LIKE_FAMILIES);
  });

  it("includes all previously missing families (was only 3, now 8)", () => {
    const previouslyMissing = [
      "investigation",
      "workflow",
      "negotiation",
      "schema_evolution",
      "tool_fragility",
    ];
    for (const family of previouslyMissing) {
      expect(SIMULATION_FAMILIES.has(family)).toBe(true);
    }
  });

  it("still includes the original 3 families", () => {
    expect(SIMULATION_FAMILIES.has("simulation")).toBe(true);
    expect(SIMULATION_FAMILIES.has("operator_loop")).toBe(true);
    expect(SIMULATION_FAMILIES.has("coordination")).toBe(true);
  });
});
