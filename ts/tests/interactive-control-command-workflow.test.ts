import { describe, expect, it, vi } from "vitest";

import {
  buildRunAcceptedMessage,
  executeInteractiveControlCommand,
} from "../src/server/interactive-control-command-workflow.js";

describe("interactive control command workflow", () => {
  it("builds run accepted messages", () => {
    expect(
      buildRunAcceptedMessage({
        runId: "run_1",
        scenario: "grid_ctf",
        generations: 3,
      }),
    ).toEqual({
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

    await expect(
      executeInteractiveControlCommand({
        command: { type: "pause" },
        runManager,
      }),
    ).resolves.toEqual([{ type: "ack", action: "pause" }]);
    expect(runManager.pause).toHaveBeenCalledOnce();

    await expect(
      executeInteractiveControlCommand({
        command: { type: "resume" },
        runManager,
      }),
    ).resolves.toEqual([{ type: "ack", action: "resume" }]);
    expect(runManager.resume).toHaveBeenCalledOnce();

    await expect(
      executeInteractiveControlCommand({
        command: { type: "inject_hint", text: "Focus on rollback safety" },
        runManager,
      }),
    ).resolves.toEqual([{ type: "ack", action: "inject_hint" }]);
    expect(runManager.injectHint).toHaveBeenCalledWith("Focus on rollback safety", []);

    await expect(
      executeInteractiveControlCommand({
        command: { type: "override_gate", decision: "rollback" },
        runManager,
      }),
    ).resolves.toEqual([{ type: "ack", action: "override_gate", decision: "rollback" }]);
    expect(runManager.overrideGate).toHaveBeenCalledWith("rollback");
  });

  it("pins an injected hint to the run that was active before image validation", async () => {
    const injectHint = vi.fn();
    await expect(executeInteractiveControlCommand({
      command: { type: "inject_hint", text: "run-scoped" },
      runManager: {
        pause: vi.fn(),
        resume: vi.fn(),
        injectHint,
        overrideGate: vi.fn(),
        startRun: vi.fn(),
        getState: () => ({ runId: "run-before-decode" }),
        getEnvironmentInfo: vi.fn(),
      },
    })).resolves.toEqual([{ type: "ack", action: "inject_hint" }]);
    expect(injectHint).toHaveBeenCalledWith("run-scoped", [], "run-before-decode");
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

    await expect(
      executeInteractiveControlCommand({
        command: {
          type: "start_run",
          scenario: "grid_ctf",
          generations: 3,
          require_playbook_approval: false,
        },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "run_accepted",
        run_id: "run_1",
        scenario: "grid_ctf",
        generations: 3,
      },
    ]);

    await expect(
      executeInteractiveControlCommand({
        command: { type: "list_scenarios" },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "environments",
        scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
        executors: [{ mode: "local", available: true, description: "Local executor" }],
        current_executor: "local",
        agent_provider: "deterministic",
      },
    ]);
  });

  it("echoes client run and command correlation on operator responses", async () => {
    const runManager = {
      pause: vi.fn(),
      resume: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
      startRun: vi.fn(async () => "engine-run-1"),
      getEnvironmentInfo: vi.fn(),
    };

    await expect(
      executeInteractiveControlCommand({
        command: {
          type: "pause",
          client_run_id: "client-run-1",
          command_id: "command-pause-1",
        },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "ack",
        action: "pause",
        client_run_id: "client-run-1",
        command_id: "command-pause-1",
      },
    ]);

    await expect(
      executeInteractiveControlCommand({
        command: {
          type: "start_run",
          scenario: "grid_ctf",
          generations: 1,
          require_playbook_approval: false,
          client_run_id: "client-run-1",
          command_id: "command-start-1",
        },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "run_accepted",
        run_id: "engine-run-1",
        scenario: "grid_ctf",
        generations: 1,
        client_run_id: "client-run-1",
        command_id: "command-start-1",
      },
    ]);
  });

  it("reports the effective minimum supplied by the active run", async () => {
    const startRun = vi.fn(async () => "run_2");
    const runManager = {
      pause: vi.fn(),
      resume: vi.fn(),
      injectHint: vi.fn(),
      overrideGate: vi.fn(),
      startRun,
      getState: () => ({
        runId: "run_2",
        minimumGenerations: 3,
        targetGenerations: 6,
      }),
      getEnvironmentInfo: vi.fn(),
    };

    await expect(
      executeInteractiveControlCommand({
        command: {
          type: "start_run",
          scenario: "saved_task",
          minimum_generations: 3,
          generations: 6,
          require_playbook_approval: false,
        },
        runManager,
      }),
    ).resolves.toEqual([
      {
        type: "run_accepted",
        run_id: "run_2",
        scenario: "saved_task",
        minimum_generations: 3,
        generations: 6,
      },
    ]);
    expect(startRun).toHaveBeenCalledWith("saved_task", 6, {
      requirePlaybookApproval: false,
      minimumGenerations: 3,
    });
  });

  it("treats a canonical null minimum as the default floor", async () => {
    const startRun = vi.fn(async () => "run_3");
    await expect(
      executeInteractiveControlCommand({
        command: {
          type: "start_run",
          scenario: "grid_ctf",
          minimum_generations: null,
          generations: 2,
          require_playbook_approval: false,
        },
        runManager: {
          pause: vi.fn(),
          resume: vi.fn(),
          injectHint: vi.fn(),
          overrideGate: vi.fn(),
          startRun,
          getEnvironmentInfo: vi.fn(),
        },
      }),
    ).resolves.toEqual([
      {
        type: "run_accepted",
        run_id: "run_3",
        scenario: "grid_ctf",
        generations: 2,
      },
    ]);
    expect(startRun).toHaveBeenCalledWith("grid_ctf", 2, {
      requirePlaybookApproval: false,
    });
  });
});
