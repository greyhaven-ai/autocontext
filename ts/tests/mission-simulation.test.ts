/**
 * AC-455: Mission-simulation integration.
 *
 * Tests that missions can invoke simulations as planning tools,
 * feed results back into planning, and track simulation cost in budget.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  SimulationAwarePlanner,
  type SimulationStepPlan,
} from "../src/mission/simulation-bridge.js";
import { MissionManager } from "../src/mission/manager.js";
import { adaptiveRunMissionLoop } from "../src/mission/adaptive-executor.js";
import { runMissionLoop } from "../src/mission/control-plane.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(responses?: string[]): LLMProvider {
  let callIndex = 0;
  const defaultDecompose = JSON.stringify({
    subgoals: [
      { description: "Evaluate pricing options via simulation", priority: 1 },
      { description: "Choose optimal pricing", priority: 2 },
    ],
  });
  const defaultStep = JSON.stringify({
    nextStep: "Run a simulation to evaluate pricing strategies",
    reasoning: "Need data before committing to a pricing decision",
    shouldRevise: false,
    simulateFirst: {
      description: "Simulate three pricing strategies and compare conversion rates",
      variables: { pricePoint: 29.99 },
    },
  });
  const defaults = [defaultDecompose, defaultStep, defaultStep];
  return {
    complete: async () => {
      const text = responses?.[callIndex % (responses?.length ?? 1)] ?? defaults[callIndex % defaults.length];
      callIndex++;
      return { text };
    },
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

let tmpDir: string;
let dbPath: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-455-test-"));
  dbPath = join(tmpDir, "missions.db");
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// SimulationAwarePlanner
// ---------------------------------------------------------------------------

describe("SimulationAwarePlanner", () => {
  it("detects when a step plan requests simulation", async () => {
    const provider = mockProvider([
      JSON.stringify({
        nextStep: "Simulate deployment scenarios",
        reasoning: "Need to compare rollback strategies",
        shouldRevise: false,
        simulateFirst: {
          description: "Simulate deployment with and without rollback",
        },
      }),
    ]);

    const planner = new SimulationAwarePlanner(provider, tmpDir);
    const step = await planner.planNextStep({
      goal: "Ship deployment pipeline",
      completedSteps: [],
      remainingSubgoals: ["Evaluate deployment strategies"],
    });

    expect(step.simulateFirst).toBeDefined();
    expect(step.simulateFirst!.description).toContain("deployment");
  });

  it("preserves targetSubgoal semantics from MissionPlanner", async () => {
    const explicitProvider = mockProvider([
      JSON.stringify({
        nextStep: "Simulate deployment scenarios",
        reasoning: "Need to compare rollback strategies",
        shouldRevise: false,
        targetSubgoal: "Evaluate deployment strategies",
        simulateFirst: {
          description: "Simulate deployment with and without rollback",
        },
      }),
    ]);

    const explicitPlanner = new SimulationAwarePlanner(explicitProvider, tmpDir);
    const explicit = await explicitPlanner.planNextStep({
      goal: "Ship deployment pipeline",
      completedSteps: [],
      remainingSubgoals: ["Evaluate deployment strategies"],
    });
    expect(explicit.targetSubgoal).toBe("Evaluate deployment strategies");

    const fallbackProvider = mockProvider([
      JSON.stringify({
        nextStep: "Run a simulation before deciding",
        reasoning: "Only one subgoal remains",
        shouldRevise: false,
        simulateFirst: {
          description: "Simulate the remaining approach",
        },
      }),
    ]);

    const fallbackPlanner = new SimulationAwarePlanner(fallbackProvider, tmpDir);
    const fallback = await fallbackPlanner.planNextStep({
      goal: "Finish rollout",
      completedSteps: [],
      remainingSubgoals: ["Choose final rollout strategy"],
    });
    expect(fallback.targetSubgoal).toBe("Choose final rollout strategy");
  });

  it("runs the requested simulation and includes results in step", async () => {
    const simSpec = JSON.stringify({
      description: "Test sim",
      environment_description: "Env",
      initial_state_description: "Start",
      success_criteria: ["done"],
      failure_modes: ["timeout"],
      max_steps: 5,
      actions: [
        { name: "act", description: "Do", parameters: {}, preconditions: [], effects: [] },
      ],
    });
    const stepWithSim = JSON.stringify({
      nextStep: "Simulate first",
      reasoning: "Need data",
      shouldRevise: false,
      simulateFirst: { description: "Run a test simulation" },
    });

    const provider = mockProvider([stepWithSim, simSpec]);
    const planner = new SimulationAwarePlanner(provider, tmpDir);

    const step = await planner.planAndSimulate({
      goal: "Test goal",
      completedSteps: [],
      remainingSubgoals: ["Do something"],
    });

    expect(step.simulationResult).toBeDefined();
    expect(step.simulationResult!.status).toBe("completed");
    expect(typeof step.simulationResult!.summary.score).toBe("number");
  });

  it("passes through normally when no simulation requested", async () => {
    const provider = mockProvider([
      JSON.stringify({
        nextStep: "Just do the work directly",
        reasoning: "No simulation needed",
        shouldRevise: false,
      }),
    ]);

    const planner = new SimulationAwarePlanner(provider, tmpDir);
    const step = await planner.planAndSimulate({
      goal: "Simple goal",
      completedSteps: [],
      remainingSubgoals: ["Do it"],
    });

    expect(step.simulateFirst).toBeUndefined();
    expect(step.simulationResult).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Budget accounting
// ---------------------------------------------------------------------------

describe("simulation budget tracking", () => {
  it("live mission run uses the simulation-aware planner and records simulation work separately", async () => {
    const simSpec = JSON.stringify({
      description: "Budget sim",
      environment_description: "Env",
      initial_state_description: "Start",
      success_criteria: ["done"],
      failure_modes: ["timeout"],
      max_steps: 3,
      actions: [
        { name: "act", description: "Do", parameters: {}, preconditions: [], effects: [] },
      ],
    });
    const stepWithSim = JSON.stringify({
      nextStep: "Choose the strongest pricing option",
      reasoning: "Need data before committing",
      shouldRevise: false,
      targetSubgoal: "Choose optimal pricing",
      simulateFirst: { description: "Run a sim" },
    });

    const provider = mockProvider([
      JSON.stringify({ subgoals: [{ description: "Choose optimal pricing", priority: 1 }] }),
      stepWithSim,
      simSpec,
    ]);

    const manager = new MissionManager(dbPath);
    const missionId = manager.create({
      name: "Simulation integration test",
      goal: "Pick the best pricing strategy",
      budget: { maxSteps: 10 },
    });

    const result = await runMissionLoop(manager, missionId, tmpDir, tmpDir, {
      maxIterations: 1,
      provider,
    });

    const steps = manager.steps(missionId);
    expect(result.finalStatus).toBe("completed");
    expect(result.stepsExecuted).toBe(2);
    expect(steps).toHaveLength(2);
    expect(steps[0]!.description).toContain("Simulate:");
    expect(steps[1]!.description).toContain("Choose the strongest pricing option");

    const simulationStepResult = JSON.parse(steps[0]!.result ?? "{}") as Record<string, unknown>;
    expect(simulationStepResult.status).toBe("completed");
    expect(typeof simulationStepResult.reportPath).toBe("string");

    const simulationsRoot = join(tmpDir, "_simulations");
    expect(existsSync(simulationsRoot)).toBe(true);
    expect(readdirSync(simulationsRoot).length).toBeGreaterThan(0);
    expect(manager.budgetUsage(missionId).stepsUsed).toBe(2);

    manager.close();
  });
});

// ---------------------------------------------------------------------------
// SimulationStepPlan type
// ---------------------------------------------------------------------------

describe("SimulationStepPlan shape", () => {
  it("extends StepPlan with simulation fields", async () => {
    const provider = mockProvider([
      JSON.stringify({
        nextStep: "Simulate",
        reasoning: "Because",
        shouldRevise: false,
        simulateFirst: { description: "Sim" },
      }),
    ]);

    const planner = new SimulationAwarePlanner(provider, tmpDir);
    const step: SimulationStepPlan = await planner.planNextStep({
      goal: "G",
      completedSteps: [],
      remainingSubgoals: [],
    });

    expect(typeof step.description).toBe("string");
    expect(typeof step.reasoning).toBe("string");
    expect(typeof step.shouldRevise).toBe("boolean");
    // simulateFirst is optional
    expect(step.simulateFirst === undefined || typeof step.simulateFirst === "object").toBe(true);
  });
});
