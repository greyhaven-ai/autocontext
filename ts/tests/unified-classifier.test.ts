/**
 * AC-437: Verify unified family classifier for the plain-language
 * custom-scenario creation path.
 *
 * `detectScenarioFamily()` should use the weighted classifier so all
 * custom-scenario-supported families are reachable, while still avoiding
 * unsupported auto-routing into the `game` family.
 */

import { describe, it, expect } from "vitest";
import { classifyScenarioFamily, routeToFamily } from "../src/scenarios/family-classifier.js";
import { detectScenarioFamily } from "../src/scenarios/scenario-creator.js";
import type { ScenarioFamilyName } from "../src/scenarios/families.js";

// ---------------------------------------------------------------------------
// The sophisticated classifier already works — baseline
// ---------------------------------------------------------------------------

describe("classifyScenarioFamily (sophisticated)", () => {
  const familyTestCases: Array<{ description: string; expected: ScenarioFamilyName }> = [
    { description: "Deploy a multi-stage pipeline with rollback and fault injection", expected: "simulation" },
    { description: "Write a comprehensive code review for a pull request", expected: "agent_task" },
    { description: "Investigate a production crash by gathering logs and diagnosing root cause", expected: "investigation" },
    { description: "Edit a YAML config file to add new service endpoints", expected: "artifact_editing" },
    { description: "Execute a multi-step payment processing workflow with compensation", expected: "workflow" },
    { description: "Negotiate a price between buyer and seller agents", expected: "negotiation" },
    { description: "Handle schema evolution when the data model changes and context becomes stale", expected: "schema_evolution" },
    { description: "Test agent behavior when tool drift causes API contract changes requiring adaptation", expected: "tool_fragility" },
    { description: "Test when agents should escalate to a human operator vs act autonomously", expected: "operator_loop" },
    { description: "Coordinate multiple agents with partial context doing handoffs and merges", expected: "coordination" },
  ];

  for (const { description, expected } of familyTestCases) {
    it(`routes "${description.slice(0, 50)}..." to ${expected}`, () => {
      const result = classifyScenarioFamily(description);
      const family = routeToFamily(result, 0.1); // low threshold for test stability
      expect(family).toBe(expected);
    });
  }
});

// ---------------------------------------------------------------------------
// detectScenarioFamily should match classifyScenarioFamily for all
// custom-scenario-supported families
// ---------------------------------------------------------------------------

describe("detectScenarioFamily routes all custom-scenario families (AC-437)", () => {
  it("routes artifact_editing descriptions correctly", () => {
    const family = detectScenarioFamily(
      "Edit a YAML config file to add new service endpoints and validate the schema",
    );
    expect(family).toBe("artifact_editing");
  });

  it("routes schema_evolution descriptions correctly", () => {
    const family = detectScenarioFamily(
      "Handle schema evolution when the data model changes and stale context must be detected",
    );
    expect(family).toBe("schema_evolution");
  });

  it("routes tool_fragility descriptions correctly", () => {
    const family = detectScenarioFamily(
      "Test agent behavior when tool drift causes API contract changes requiring adaptation",
    );
    expect(family).toBe("tool_fragility");
  });

  it("routes operator_loop descriptions correctly", () => {
    const family = detectScenarioFamily(
      "Test when agents should escalate to a human operator versus acting autonomously with clarification requests",
    );
    expect(family).toBe("operator_loop");
  });

  it("routes coordination descriptions correctly", () => {
    const family = detectScenarioFamily(
      "Coordinate multiple agents with partial context doing handoffs and merge operations",
    );
    expect(family).toBe("coordination");
  });

  // These 6 families already work — regression guard
  it("routes simulation descriptions correctly", () => {
    const family = detectScenarioFamily("Deploy a pipeline with fault injection and orchestration");
    expect(family).toBe("simulation");
  });

  it("routes investigation descriptions correctly", () => {
    const family = detectScenarioFamily("Investigate a crash by debugging and diagnosing root cause");
    expect(family).toBe("investigation");
  });

  it("routes workflow descriptions correctly", () => {
    const family = detectScenarioFamily("Execute a multi-step transaction workflow with compensation and rollback");
    expect(family).toBe("workflow");
  });

  it("routes negotiation descriptions correctly", () => {
    const family = detectScenarioFamily("Negotiate a trade deal between two parties bargaining over price");
    expect(family).toBe("negotiation");
  });

  it("defaults to agent_task for generic descriptions", () => {
    const family = detectScenarioFamily("Write a summary of the quarterly earnings report");
    expect(family).toBe("agent_task");
  });

  it("does not auto-route unsupported custom game creation into game", () => {
    const family = detectScenarioFamily(
      "Create a two-player board game with scoring and turns",
    );
    expect(family).toBe("agent_task");
  });
});

// ---------------------------------------------------------------------------
// Consistency: supported families agree with the weighted classifier
// ---------------------------------------------------------------------------

describe("classifier consistency (AC-437 + AC-444)", () => {
  const descriptions = [
    "Deploy a multi-stage pipeline with rollback",
    "Write a code review for a PR",
    "Investigate a production outage root cause",
    "Edit config files to update service endpoints",
    "Run a multi-step payment workflow with compensation",
    "Negotiate pricing between buyer and seller",
    "Handle schema evolution when data model changes with stale context",
    "Test tool drift when API contract changes require adaptation",
    "Decide when to escalate to human operator",
    "Coordinate agents with partial context and handoffs",
  ];

  for (const desc of descriptions) {
    it(`both classifiers agree on "${desc.slice(0, 40)}..."`, () => {
      const sophisticated = routeToFamily(classifyScenarioFamily(desc), 0.1);
      const naive = detectScenarioFamily(desc);
      expect(naive).toBe(sophisticated);
    });
  }

  it("intentionally diverges for game because game is not a supported custom-scenario target", () => {
    const desc = "Create a two-player board game with scoring and turns";
    const sophisticated = routeToFamily(classifyScenarioFamily(desc), 0.1);
    const detected = detectScenarioFamily(desc);
    expect(sophisticated).toBe("game");
    expect(detected).toBe("agent_task");
  });
});
