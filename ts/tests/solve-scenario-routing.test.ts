import { beforeEach, describe, expect, it, afterEach } from "vitest";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  determineSolveExecutionRoute,
  persistSolveScenarioScaffold,
  prepareSolveScenario,
  resolveSolveFamilyAlias,
  resolveSolveFamilyHint,
  resolveSolveFamilyOverride,
  validateSolveFamilyOverride,
} from "../src/knowledge/solve-scenario-routing.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-solve-routing-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("solve scenario routing", () => {
  it("prepares created scenarios by coercing unsupported families to agent_task", () => {
    const prepared = prepareSolveScenario({
      description: "Summarize incident reports",
      created: {
        name: "incident_summary",
        family: "unsupported_family",
        spec: {
          taskPrompt: "Summarize incident reports",
          rubric: "Evaluate completeness",
          description: "Incident summary task",
        },
      },
    });

    expect(prepared.family).toBe("agent_task");
    expect(prepared.spec.description).toBe("Incident summary task");
  });

  it("honors explicit family overrides without mutating the description", () => {
    expect(validateSolveFamilyOverride("operator-loop")).toBe("operator_loop");
    expect(() => validateSolveFamilyOverride("nope")).toThrow("Unknown solve family");
    expect(resolveSolveFamilyOverride("**Family:** meta_learning", "simulation")).toBe("simulation");

    const prepared = prepareSolveScenario({
      description: "Investigate a production outage",
      familyOverride: "investigation",
      created: {
        name: "outage_summary",
        family: "agent_task",
        spec: {
          taskPrompt: "Summarize outage reports",
          rubric: "Evaluate completeness",
          description: "Outage task",
        },
      },
    });

    expect(prepared.family).toBe("investigation");
  });

  it("preserves Python solve-specific family aliases and interface hints", () => {
    expect(resolveSolveFamilyHint("Family: investigation\n\nSummarize checkout failures.")).toBe(
      "investigation",
    );
    expect(resolveSolveFamilyHint("Family: simulation / workflow\n\nModel state transitions.")).toBe(
      "simulation",
    );
    expect(resolveSolveFamilyOverride("Family: operator loop\n\nEscalate when blocked.")).toBe(
      "operator_loop",
    );
    expect(
      resolveSolveFamilyAlias(
        [
          "## Scenario Proposal",
          "",
          "**Family:** alignment_stress_test",
          "",
          "The system is given a scoring function with a known exploit.",
        ].join("\n"),
      ),
    ).toBe("agent_task");
    expect(
      resolveSolveFamilyAlias(
        "Build a clinical trial harness. Use `SimulationInterface` + `WorldState` for state.",
      ),
    ).toBe("simulation");
    expect(
      resolveSolveFamilyAlias(
        "Use agent-task evaluation with structured output.",
      ),
    ).toBe("agent_task");
  });

  it("routes prepared scenarios through explicit execution paths", () => {
    expect(
      determineSolveExecutionRoute(
        {
          name: "grid_ctf",
          family: "game",
          spec: { taskPrompt: "builtin", rubric: "builtin", description: "builtin" },
        },
        ["grid_ctf"],
      ),
    ).toBe("builtin_game");

    expect(
      determineSolveExecutionRoute(
        {
          name: "custom_game",
          family: "game",
          spec: { taskPrompt: "missing", rubric: "missing", description: "missing" },
        },
        ["grid_ctf"],
      ),
    ).toBe("missing_game");

    expect(
      determineSolveExecutionRoute(
        {
          name: "incident_summary",
          family: "agent_task",
          spec: { taskPrompt: "task", rubric: "task", description: "task" },
        },
        ["grid_ctf"],
      ),
    ).toBe("agent_task");

    expect(
      determineSolveExecutionRoute(
        {
          name: "outage_investigation",
          family: "investigation",
          spec: { taskPrompt: "investigate", rubric: "investigate", description: "investigate", actions: [] },
        },
        ["grid_ctf"],
      ),
    ).toBe("codegen");
  });

  it("persists agent_task scaffolds with custom-loader compatible files", async () => {
    const persisted = await persistSolveScenarioScaffold({
      created: {
        name: "incident_summary",
        family: "agent_task",
        spec: {
          taskPrompt: "Summarize incident reports",
          rubric: "Evaluate completeness",
          description: "Incident summary task",
        },
      },
      knowledgeRoot: tmpDir,
    });

    expect(persisted.persisted).toBe(true);
    const scenarioDir = join(tmpDir, "_custom_scenarios", "incident_summary");
    expect(existsSync(join(scenarioDir, "scenario_type.txt"))).toBe(true);
    expect(existsSync(join(scenarioDir, "agent_task_spec.json"))).toBe(true);
  });

  it("persists missing built-in games as dead-end scaffolds for diagnostics", async () => {
    const persisted = await persistSolveScenarioScaffold({
      created: {
        name: "custom_game",
        family: "game",
        spec: {
          taskPrompt: "Create a board game",
          rubric: "Evaluate fairness",
          description: "Custom board game",
        },
      },
      knowledgeRoot: tmpDir,
    });

    expect(persisted.persisted).toBe(true);
    const scenarioDir = join(tmpDir, "_custom_scenarios", "custom_game");
    expect(readFileSync(join(scenarioDir, "scenario_type.txt"), "utf-8").trim()).toBe("parametric");
    const spec = JSON.parse(readFileSync(join(scenarioDir, "spec.json"), "utf-8")) as Record<string, unknown>;
    expect(spec.family).toBe("game");
    expect(spec.taskPrompt).toBe("Create a board game");
  });
});
