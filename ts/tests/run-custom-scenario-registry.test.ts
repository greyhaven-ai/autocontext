import { describe, expect, it, vi } from "vitest";

import type { CustomScenarioEntry } from "../src/scenarios/custom-loader.js";
import { RunCustomScenarioRegistry } from "../src/server/run-custom-scenario-registry.js";

describe("run custom scenario registry", () => {
  it("reloads custom scenarios from the knowledge root and registers them", () => {
    const loaded = new Map<string, CustomScenarioEntry>([
      [
        "saved_task",
        {
          name: "saved_task",
          type: "agent_task",
          spec: { taskPrompt: "Summarize incidents." },
          path: "/tmp/knowledge/_custom_scenarios/saved_task",
          hasGeneratedSource: false,
        },
      ],
    ]);
    const loadCustomScenarios = vi.fn(() => loaded);
    const registerCustomScenarios = vi.fn();

    const registry = new RunCustomScenarioRegistry({
      knowledgeRoot: "/tmp/knowledge",
      deps: {
        loadCustomScenarios,
        registerCustomScenarios,
      },
    });

    registry.reload();

    expect(loadCustomScenarios).toHaveBeenCalledWith("/tmp/knowledge/_custom_scenarios");
    expect(registerCustomScenarios).toHaveBeenCalledWith(loaded);
    expect(registry.get("saved_task")).toEqual(loaded.get("saved_task"));
  });

  it("returns values for environment and start-run lookups", () => {
    const registry = new RunCustomScenarioRegistry({
      knowledgeRoot: "/tmp/knowledge",
      deps: {
        loadCustomScenarios: () => new Map<string, CustomScenarioEntry>([
          [
            "saved_sim",
            {
              name: "saved_sim",
              type: "simulation",
              spec: { description: "Saved simulation" },
              path: "/tmp/knowledge/_custom_scenarios/saved_sim",
              hasGeneratedSource: true,
            },
          ],
        ]),
        registerCustomScenarios: () => {},
      },
    });

    registry.reload();

    expect([...registry.values()].map((entry) => entry.name)).toEqual(["saved_sim"]);
    expect(registry.get("missing")).toBeUndefined();
  });
});
