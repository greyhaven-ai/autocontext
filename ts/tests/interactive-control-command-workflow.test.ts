import { describe, expect, it, vi } from "vitest";

import {
  buildRunAcceptedMessage,
  executeInteractiveControlCommand,
} from "../src/server/interactive-control-command-workflow.js";

describe("interactive control command workflow", () => {
  it("builds run accepted messages", () => {
    expect(buildRunAcceptedMessage({
      runId: "run_1",
      scenario: "grid_ctf",
      generations: 3,
    })).toEqual({
      type: "run_accepted",
      run_id: "run_1",
      scenario: "grid_ctf",
      generations: 3,
    });
  });

  it("executes pause, resume, inject_hint, and override_gate commands", async () => {
    const runManager = {
      pause: vi.fn(),
      resume: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
      startRun: vi.fn(),
      getEnvironmentInfo: vi.fn(),
    };

    await expect(executeInteractiveControlCommand({
      command: { type: "pause" },
      runManager,
    })).resolves.toEqual([{ type: "ack", action: "pause" }]);
    expect(runManager.pause).toHaveBeenCalledOnce();

    await expect(executeInteractiveControlCommand({
      command: { type: "resume" },
      runManager,
    })).resolves.toEqual([{ type: "ack", action: "resume" }]);
    expect(runManager.resume).toHaveBeenCalledOnce();

    await expect(executeInteractiveControlCommand({
      command: { type: "inject_hint", text: "Focus on rollback safety" },
      runManager,
    })).resolves.toEqual([{ type: "ack", action: "inject_hint" }]);
    expect(runManager.injectHint).toHaveBeenCalledWith("Focus on rollback safety");

    await expect(executeInteractiveControlCommand({
      command: { type: "override_gate", decision: "rollback" },
      runManager,
    })).resolves.toEqual([{ type: "ack", action: "override_gate", decision: "rollback" }]);
    expect(runManager.overrideGate).toHaveBeenCalledWith("rollback");
  });

  it("executes start_run and list_scenarios commands", async () => {
    const runManager = {
      pause: vi.fn(),
      resume: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
      startRun: vi.fn(async () => "run_1"),
      getEnvironmentInfo: vi.fn(() => ({
        scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
        executors: [{ mode: "local", available: true, description: "Local executor" }],
        currentExecutor: "local",
        agentProvider: "deterministic",
      })),
    };

    await expect(executeInteractiveControlCommand({
      command: { type: "start_run", scenario: "grid_ctf", generations: 3 },
      runManager,
    })).resolves.toEqual([
      {
        type: "run_accepted",
        run_id: "run_1",
        scenario: "grid_ctf",
        generations: 3,
      },
    ]);

    await expect(executeInteractiveControlCommand({
      command: { type: "list_scenarios" },
      runManager,
    })).resolves.toEqual([
      {
        type: "environments",
        scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
        executors: [{ mode: "local", available: true, description: "Local executor" }],
        current_executor: "local",
        agent_provider: "deterministic",
      },
    ]);
  });
});
