import { describe, expect, it, vi } from "vitest";

import {
  executeMissionArtifactsCommand,
  executeMissionCreateCommand,
  executeMissionLifecycleCommand,
  executeMissionListCommand,
  executeMissionRunCommand,
  executeMissionStatusCommand,
} from "../src/cli/mission-command-execution.js";
import type { LLMProvider } from "../src/types/index.js";

describe("mission command execution", () => {
  it("creates generic missions and returns checkpoint payloads", () => {
    const create = vi.fn(() => "mission-1");
    const createCodeMission = vi.fn();
    const buildMissionStatusPayload = vi.fn(() => ({
      id: "mission-1",
      status: "active",
    }));
    const writeMissionCheckpoint = vi.fn(() => "/runs/missions/mission-1/checkpoint.json");

    expect(
      executeMissionCreateCommand({
        manager: { create },
        createCodeMission,
        buildMissionStatusPayload,
        writeMissionCheckpoint,
        runsRoot: "/runs",
        plan: {
          missionType: "generic",
          name: "Ship login",
          goal: "Implement OAuth",
          budget: { maxSteps: 5 },
        },
      }),
    ).toEqual({
      id: "mission-1",
      status: "active",
      checkpointPath: "/runs/missions/mission-1/checkpoint.json",
    });

    expect(create).toHaveBeenCalledWith({
      name: "Ship login",
      goal: "Implement OAuth",
      budget: { maxSteps: 5 },
    });
    expect(createCodeMission).not.toHaveBeenCalled();
  });

  it("creates code missions through the dedicated factory", () => {
    const manager = { create: vi.fn() };
    const createCodeMission = vi.fn(() => "mission-code");

    executeMissionCreateCommand({
      manager,
      createCodeMission,
      buildMissionStatusPayload: () => ({ id: "mission-code", status: "active" }),
      writeMissionCheckpoint: () => "/runs/missions/mission-code/checkpoint.json",
      runsRoot: "/runs",
      plan: {
        missionType: "code",
        name: "Fix login",
        goal: "Tests pass",
        budget: { maxSteps: 3 },
        repoPath: "/repo",
        testCommand: "npm test",
        lintCommand: "npm run lint",
        buildCommand: "npm run build",
      },
    });

    expect(createCodeMission).toHaveBeenCalledWith(manager, {
      name: "Fix login",
      goal: "Tests pass",
      repoPath: "/repo",
      testCommand: "npm test",
      lintCommand: "npm run lint",
      buildCommand: "npm run build",
      budget: { maxSteps: 3 },
      metadata: {},
    });
  });

  it("runs missions without adaptive providers when not needed", async () => {
    const createAdaptiveProvider = vi.fn();
    const runMissionLoop = vi.fn(async () => ({
      id: "mission-1",
      finalStatus: "completed",
    }));

    await expect(
      executeMissionRunCommand({
        manager: { tag: "manager" },
        plan: {
          id: "mission-1",
          maxIterations: 2,
          stepDescription: "Inspect auth flow",
          needsAdaptivePlanning: false,
        },
        runsRoot: "/runs",
        knowledgeRoot: "/knowledge",
        createAdaptiveProvider,
        runMissionLoop,
      }),
    ).resolves.toEqual({
      id: "mission-1",
      finalStatus: "completed",
    });

    expect(createAdaptiveProvider).not.toHaveBeenCalled();
    expect(runMissionLoop).toHaveBeenCalledWith(
      { tag: "manager" },
      "mission-1",
      "/runs",
      "/knowledge",
      {
        maxIterations: 2,
        stepDescription: "Inspect auth flow",
        provider: undefined,
      },
    );
  });

  it("runs missions with adaptive providers when required", async () => {
    const provider: LLMProvider = {
      name: "provider",
      complete: async () => ({ text: "", usage: {} }),
      defaultModel: () => "test-model",
    };
    const createAdaptiveProvider = vi.fn(() => provider);
    const runMissionLoop = vi.fn(async () => ({
      id: "mission-2",
      finalStatus: "completed",
      planGenerated: true,
    }));

    await executeMissionRunCommand({
      manager: { tag: "manager" },
      plan: {
        id: "mission-2",
        maxIterations: 1,
        stepDescription: undefined,
        needsAdaptivePlanning: true,
      },
      runsRoot: "/runs",
      knowledgeRoot: "/knowledge",
      createAdaptiveProvider,
      runMissionLoop,
    });

    expect(createAdaptiveProvider).toHaveBeenCalledOnce();
    expect(runMissionLoop).toHaveBeenCalledWith(
      { tag: "manager" },
      "mission-2",
      "/runs",
      "/knowledge",
      {
        maxIterations: 1,
        stepDescription: undefined,
        provider,
      },
    );
  });

  it("builds mission status and artifacts through shared payload helpers", () => {
    expect(
      executeMissionStatusCommand({
        manager: { tag: "manager" },
        missionId: "mission-1",
        buildMissionStatusPayload: (_manager, missionId) => ({
          id: missionId,
          status: "active",
        }),
      }),
    ).toEqual({ id: "mission-1", status: "active" });

    expect(
      executeMissionArtifactsCommand({
        manager: { tag: "manager" },
        missionId: "mission-1",
        runsRoot: "/runs",
        buildMissionArtifactsPayload: (_manager, missionId, runsRoot) => ({
          missionId,
          checkpointDir: `${runsRoot}/missions/${missionId}`,
        }),
      }),
    ).toEqual({
      missionId: "mission-1",
      checkpointDir: "/runs/missions/mission-1",
    });
  });

  it("lists missions by optional status", () => {
    const list = vi.fn(() => [{ id: "mission-1" }]);

    expect(
      executeMissionListCommand({
        listMissions: list,
        status: "active",
      }),
    ).toEqual([{ id: "mission-1" }]);
    expect(list).toHaveBeenCalledWith("active");
  });

  it("applies lifecycle actions and returns checkpoint payloads", () => {
    const pause = vi.fn();
    const buildMissionStatusPayload = vi.fn(() => ({
      id: "mission-1",
      status: "paused",
    }));
    const writeMissionCheckpoint = vi.fn(() => "/runs/missions/mission-1/checkpoint.json");

    expect(
      executeMissionLifecycleCommand({
        action: "pause",
        missionId: "mission-1",
        manager: { pause, resume: vi.fn(), cancel: vi.fn() },
        buildMissionStatusPayload,
        writeMissionCheckpoint,
        runsRoot: "/runs",
      }),
    ).toEqual({
      id: "mission-1",
      status: "paused",
      checkpointPath: "/runs/missions/mission-1/checkpoint.json",
    });

    expect(pause).toHaveBeenCalledWith("mission-1");
  });
});
