import { describe, expect, it, vi } from "vitest";

import { planMaterializeScenarioRequest } from "../src/scenarios/materialize-request-planning.js";
import type { ScenarioFamilyName } from "../src/scenarios/families.js";

describe("materialize request planning", () => {
  it("plans family, healed spec, scenario type, and scenario directory", () => {
    const coerceMaterializeFamily = vi.fn((): ScenarioFamilyName => "agent_task");
    const healSpec = vi.fn(() => ({ taskPrompt: "Write a poem" }));
    const getScenarioTypeMarker = vi.fn(() => "agent_task");

    expect(
      planMaterializeScenarioRequest({
        family: "unknown_family",
        name: "poetry_task",
        spec: { taskPrompt: "Draft poem" },
        knowledgeRoot: "/tmp/knowledge",
        coerceMaterializeFamily,
        healSpec,
        getScenarioTypeMarker,
      }),
    ).toEqual({
      family: "agent_task",
      healedSpec: { taskPrompt: "Write a poem" },
      scenarioDir: "/tmp/knowledge/_custom_scenarios/poetry_task",
      scenarioType: "agent_task",
    });

    expect(coerceMaterializeFamily).toHaveBeenCalledWith("unknown_family");
    expect(healSpec).toHaveBeenCalledWith({ taskPrompt: "Draft poem" }, "agent_task");
    expect(getScenarioTypeMarker).toHaveBeenCalledWith("agent_task");
  });
});
