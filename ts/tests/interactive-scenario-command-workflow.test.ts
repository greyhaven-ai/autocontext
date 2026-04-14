import { describe, expect, it, vi } from "vitest";

import {
  buildScenarioPreviewMessage,
  buildScenarioReadyMessage,
  executeInteractiveScenarioCommand,
} from "../src/server/interactive-scenario-command-workflow.js";

describe("interactive scenario command workflow", () => {
  it("builds scenario preview messages from preview info", () => {
    expect(buildScenarioPreviewMessage({
      name: "incident_triage",
      displayName: "Incident Triage",
      description: "Incident triage scenario",
      strategyParams: [{ name: "style", description: "Output style" }],
      scoringComponents: [{ name: "clarity", description: "Clarity", weight: 1 }],
      constraints: ["Keep summaries concise"],
      winThreshold: 0.9,
    })).toEqual({
      type: "scenario_preview",
      name: "incident_triage",
      display_name: "Incident Triage",
      description: "Incident triage scenario",
      strategy_params: [{ name: "style", description: "Output style" }],
      scoring_components: [{ name: "clarity", description: "Clarity", weight: 1 }],
      constraints: ["Keep summaries concise"],
      win_threshold: 0.9,
    });
  });

  it("builds scenario ready messages from confirmed scenario info", () => {
    expect(buildScenarioReadyMessage({
      name: "incident_triage",
      testScores: [0.95],
    })).toEqual({
      type: "scenario_ready",
      name: "incident_triage",
      test_scores: [0.95],
    });
  });

  it("executes create and revise commands with generating + preview messages", async () => {
    const runManager = {
      createScenario: vi.fn(async () => ({
        name: "incident_triage",
        displayName: "Incident Triage",
        description: "Incident triage scenario",
        strategyParams: [],
        scoringComponents: [],
        constraints: [],
        winThreshold: 0.9,
      })),
      reviseScenario: vi.fn(async () => ({
        name: "incident_triage",
        displayName: "Incident Triage",
        description: "Incident triage scenario with owner assignment",
        strategyParams: [],
        scoringComponents: [],
        constraints: [],
        winThreshold: 0.9,
      })),
      confirmScenario: vi.fn(),
      cancelScenario: vi.fn(),
    };

    await expect(executeInteractiveScenarioCommand({
      command: { type: "create_scenario", description: "Create a triage scenario" },
      runManager,
    })).resolves.toEqual([
      { type: "scenario_generating", name: "custom_scenario" },
      {
        type: "scenario_preview",
        name: "incident_triage",
        display_name: "Incident Triage",
        description: "Incident triage scenario",
        strategy_params: [],
        scoring_components: [],
        constraints: [],
        win_threshold: 0.9,
      },
    ]);

    await expect(executeInteractiveScenarioCommand({
      command: { type: "revise_scenario", feedback: "Add owner assignment" },
      runManager,
    })).resolves.toEqual([
      { type: "scenario_generating", name: "custom_scenario" },
      {
        type: "scenario_preview",
        name: "incident_triage",
        display_name: "Incident Triage",
        description: "Incident triage scenario with owner assignment",
        strategy_params: [],
        scoring_components: [],
        constraints: [],
        win_threshold: 0.9,
      },
    ]);
  });

  it("executes confirm and cancel commands with ack semantics", async () => {
    const runManager = {
      createScenario: vi.fn(),
      reviseScenario: vi.fn(),
      confirmScenario: vi.fn(async () => ({
        name: "incident_triage",
        testScores: [],
      })),
      cancelScenario: vi.fn(),
    };

    await expect(executeInteractiveScenarioCommand({
      command: { type: "confirm_scenario" },
      runManager,
    })).resolves.toEqual([
      { type: "ack", action: "confirm_scenario" },
      { type: "scenario_ready", name: "incident_triage", test_scores: [] },
    ]);

    await expect(executeInteractiveScenarioCommand({
      command: { type: "cancel_scenario" },
      runManager,
    })).resolves.toEqual([
      { type: "ack", action: "cancel_scenario" },
    ]);
    expect(runManager.cancelScenario).toHaveBeenCalledOnce();
  });
});
