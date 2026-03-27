/**
 * AC-435: Adaptive mission execution loop.
 *
 * Tests verify that missions decompose plain-language goals into subgoals,
 * execute meaningful steps, and revise plans based on verifier feedback.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  MissionPlanner,
  type PlanResult,
  type StepPlan,
} from "../src/mission/planner.js";
import { MissionManager } from "../src/mission/manager.js";
import { adaptiveRunMissionLoop } from "../src/mission/adaptive-executor.js";
import type { LLMProvider } from "../src/types/index.js";

// ---------------------------------------------------------------------------
// Mock LLM provider
// ---------------------------------------------------------------------------

function mockProvider(responses: string[]): LLMProvider {
  let callIndex = 0;
  return {
    complete: async (opts: { systemPrompt: string; userPrompt: string }) => {
      const text = responses[callIndex % responses.length] ?? "{}";
      callIndex++;
      return { text };
    },
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

let tmpDir: string;
let dbPath: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-435-test-"));
  dbPath = join(tmpDir, "missions.db");
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// Planner: goal decomposition
// ---------------------------------------------------------------------------

describe("MissionPlanner", () => {
  it("decomposes a goal into subgoals via LLM", async () => {
    const provider = mockProvider([
      JSON.stringify({
        subgoals: [
          { description: "Set up authentication module", priority: 1 },
          { description: "Implement OAuth flow", priority: 2 },
          { description: "Write integration tests", priority: 3 },
        ],
      }),
    ]);

    const planner = new MissionPlanner(provider);
    const plan = await planner.decompose("Implement user login with OAuth");

    expect(plan.subgoals.length).toBe(3);
    expect(plan.subgoals[0].description).toContain("authentication");
    expect(plan.subgoals[0].priority).toBe(1);
  });

  it("plans the next step based on goal + history + feedback", async () => {
    const provider = mockProvider([
      JSON.stringify({
        nextStep: "Run the test suite to verify OAuth integration",
        reasoning: "Previous step implemented OAuth. Now verify it works.",
        shouldRevise: false,
      }),
    ]);

    const planner = new MissionPlanner(provider);
    const step = await planner.planNextStep({
      goal: "Implement user login with OAuth",
      completedSteps: ["Set up authentication module", "Implement OAuth flow"],
      remainingSubgoals: ["Write integration tests"],
      verifierFeedback: { passed: false, reason: "Tests not yet run", suggestions: ["Run npm test"] },
    });

    expect(step.description).toContain("test");
    expect(step.reasoning).toBeTruthy();
  });

  it("signals plan revision when verifier feedback suggests it", async () => {
    const provider = mockProvider([
      JSON.stringify({
        nextStep: "Redesign the auth flow to use PKCE",
        reasoning: "Verifier found security vulnerability in current OAuth implementation",
        shouldRevise: true,
        revisedSubgoals: [
          { description: "Implement PKCE challenge flow", priority: 1 },
          { description: "Update token storage", priority: 2 },
        ],
      }),
    ]);

    const planner = new MissionPlanner(provider);
    const step = await planner.planNextStep({
      goal: "Implement secure OAuth",
      completedSteps: ["Basic OAuth flow"],
      remainingSubgoals: ["Write tests"],
      verifierFeedback: {
        passed: false,
        reason: "Security audit failed: no PKCE",
        suggestions: ["Implement PKCE", "Use secure token storage"],
      },
    });

    expect(step.shouldRevise).toBe(true);
    expect(step.revisedSubgoals).toBeDefined();
    expect(step.revisedSubgoals!.length).toBeGreaterThan(0);
  });

  it("returns fallback plan when LLM fails", async () => {
    const provider = mockProvider(["not valid json at all"]);

    const planner = new MissionPlanner(provider);
    const plan = await planner.decompose("Do something");

    // Should not throw — returns a single-subgoal fallback
    expect(plan.subgoals.length).toBeGreaterThanOrEqual(1);
  });
});

// ---------------------------------------------------------------------------
// Adaptive executor: end-to-end loop
// ---------------------------------------------------------------------------

describe("adaptiveRunMissionLoop", () => {
  it("decomposes goal, executes steps, and checks verifier", async () => {
    const decomposition = JSON.stringify({
      subgoals: [
        { description: "Step A", priority: 1 },
        { description: "Step B", priority: 2 },
      ],
    });
    const stepPlan = JSON.stringify({
      nextStep: "Execute the current subgoal",
      reasoning: "Working through plan",
      shouldRevise: false,
    });

    const provider = mockProvider([decomposition, stepPlan, stepPlan, stepPlan]);
    const manager = new MissionManager(dbPath);

    const missionId = manager.create({
      name: "Test mission",
      goal: "Build a feature end to end",
      budget: { maxSteps: 5 },
    });

    // Verifier passes after 2 steps
    let verifyCount = 0;
    manager.setVerifier(missionId, async () => {
      verifyCount++;
      return verifyCount >= 2
        ? { passed: true, reason: "All done", suggestions: [], metadata: {} }
        : { passed: false, reason: "Not done yet", suggestions: ["Keep going"], metadata: {} };
    });

    const result = await adaptiveRunMissionLoop(manager, missionId, provider, tmpDir, {
      maxIterations: 5,
    });

    expect(result.stepsExecuted).toBeGreaterThanOrEqual(2);
    expect(result.finalStatus).toBe("completed");
    expect(result.planGenerated).toBe(true);

    // Subgoals should have been created from the decomposition
    const subgoals = manager.subgoals(missionId);
    expect(subgoals.length).toBeGreaterThanOrEqual(2);

    manager.close();
  });

  it("respects budget limits", async () => {
    const provider = mockProvider([
      JSON.stringify({ subgoals: [{ description: "Work", priority: 1 }] }),
      JSON.stringify({ nextStep: "Do work", reasoning: "Working", shouldRevise: false }),
      JSON.stringify({ nextStep: "More work", reasoning: "Still going", shouldRevise: false }),
    ]);

    const manager = new MissionManager(dbPath);
    const missionId = manager.create({
      name: "Budget test",
      goal: "Infinite work",
      budget: { maxSteps: 2 },
    });

    manager.setVerifier(missionId, async () => ({
      passed: false, reason: "Never done", suggestions: [], metadata: {},
    }));

    const result = await adaptiveRunMissionLoop(manager, missionId, provider, tmpDir, {
      maxIterations: 10,
    });

    expect(result.stepsExecuted).toBeLessThanOrEqual(3); // budget + 1 for the check
    expect(["budget_exhausted", "active"]).toContain(result.finalStatus);

    manager.close();
  });

  it("revises plan when verifier suggests changes", async () => {
    let callCount = 0;
    const provider = {
      complete: async () => {
        callCount++;
        if (callCount === 1) {
          // Initial decomposition
          return { text: JSON.stringify({ subgoals: [{ description: "Original plan", priority: 1 }] }) };
        }
        if (callCount === 2) {
          // First step plan — signals revision needed
          return {
            text: JSON.stringify({
              nextStep: "Revise approach",
              reasoning: "Verifier failed, need new plan",
              shouldRevise: true,
              revisedSubgoals: [
                { description: "New approach step 1", priority: 1 },
                { description: "New approach step 2", priority: 2 },
              ],
            }),
          };
        }
        // Subsequent steps
        return { text: JSON.stringify({ nextStep: "Continue revised plan", reasoning: "On track", shouldRevise: false }) };
      },
      defaultModel: () => "test",
    } as unknown as LLMProvider;

    const manager = new MissionManager(dbPath);
    const missionId = manager.create({
      name: "Revision test",
      goal: "Adaptive goal",
      budget: { maxSteps: 5 },
    });

    let verifyCount = 0;
    manager.setVerifier(missionId, async () => {
      verifyCount++;
      return verifyCount >= 3
        ? { passed: true, reason: "Done after revision", suggestions: [], metadata: {} }
        : { passed: false, reason: "Not done", suggestions: ["Try harder"], metadata: {} };
    });

    const result = await adaptiveRunMissionLoop(manager, missionId, provider, tmpDir, {
      maxIterations: 5,
    });

    // Should have revised subgoals
    const subgoals = manager.subgoals(missionId);
    const descriptions = subgoals.map((s) => s.description);
    expect(descriptions.some((d) => d.includes("New approach"))).toBe(true);

    manager.close();
  });

  it("preserves operator controls (pause/resume/cancel)", async () => {
    const provider = mockProvider([
      JSON.stringify({ subgoals: [{ description: "Work", priority: 1 }] }),
      JSON.stringify({ nextStep: "Working", reasoning: "Go", shouldRevise: false }),
    ]);

    const manager = new MissionManager(dbPath);
    const missionId = manager.create({ name: "Control test", goal: "Test controls" });

    // Pause before running
    manager.pause(missionId);
    expect(manager.get(missionId)!.status).toBe("paused");

    // Resume
    manager.resume(missionId);
    expect(manager.get(missionId)!.status).toBe("active");

    // Cancel
    manager.cancel(missionId);
    expect(manager.get(missionId)!.status).toBe("canceled");

    manager.close();
  });
});

// ---------------------------------------------------------------------------
// PlanResult / StepPlan shapes
// ---------------------------------------------------------------------------

describe("planner types", () => {
  it("PlanResult has subgoals array", async () => {
    const provider = mockProvider([
      JSON.stringify({ subgoals: [{ description: "Do X", priority: 1 }] }),
    ]);
    const planner = new MissionPlanner(provider);
    const plan: PlanResult = await planner.decompose("Test");
    expect(Array.isArray(plan.subgoals)).toBe(true);
    expect(plan.subgoals[0]).toHaveProperty("description");
    expect(plan.subgoals[0]).toHaveProperty("priority");
  });

  it("StepPlan has description and reasoning", async () => {
    const provider = mockProvider([
      JSON.stringify({ nextStep: "Do it", reasoning: "Because", shouldRevise: false }),
    ]);
    const planner = new MissionPlanner(provider);
    const step: StepPlan = await planner.planNextStep({
      goal: "G", completedSteps: [], remainingSubgoals: [],
    });
    expect(typeof step.description).toBe("string");
    expect(typeof step.reasoning).toBe("string");
    expect(typeof step.shouldRevise).toBe("boolean");
  });
});
