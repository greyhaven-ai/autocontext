import { describe, expect, it } from "vitest";

import {
  healSimulationPreconditions,
  needsPreconditionHealing,
  normalizePreconditionToken,
} from "../src/scenarios/spec-auto-heal-preconditions.js";

describe("spec auto-heal precondition workflow", () => {
  it("identifies which families require action-name precondition healing", () => {
    expect(needsPreconditionHealing("simulation")).toBe(true);
    expect(needsPreconditionHealing("workflow")).toBe(true);
    expect(needsPreconditionHealing("agent_task")).toBe(false);
  });

  it("normalizes tokens consistently across separators", () => {
    expect(normalizePreconditionToken("Provision.Infrastructure")).toBe(
      "provision infrastructure",
    );
    expect(normalizePreconditionToken("run-tests")).toBe("run tests");
  });

  it("keeps valid action-name preconditions and strips unsatisfied prose", () => {
    const healed = healSimulationPreconditions({
      actions: [
        {
          name: "setup",
          preconditions: ["The environment is ready."],
        },
        {
          name: "deploy",
          preconditions: ["setup"],
        },
      ],
    });

    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[0].preconditions).toEqual([]);
    expect(actions[1].preconditions).toEqual(["setup"]);
  });

  it("fuzzy-matches action names across underscores, hyphens, and dots", () => {
    const healed = healSimulationPreconditions({
      actions: [
        { name: "provision_infrastructure", preconditions: [] },
        { name: "run-tests", preconditions: ["provision infrastructure"] },
        { name: "deploy", preconditions: ["run tests"] },
      ],
    });

    const actions = healed.actions as Array<{ preconditions: string[] }>;
    expect(actions[1].preconditions).toEqual(["provision_infrastructure"]);
    expect(actions[2].preconditions).toEqual(["run-tests"]);
  });
});
